r"""bake_terrain_ground.py -- bake a zone's ORIGINAL KO ground surface.

Renders the zone's own GTT tiles verbatim into a pair of files:

    <output>/terrain/<zone>/tiles.png   original GTT tiles, 128px, vertical strip atlas
    <output>/terrain/<zone>/splat.png   per-cell layer indices + stamp directions

Read at runtime by client/src/Terrain/KoGroundTex.cs — the only producer of the ground textures the
client renders.

splat.png encoding (RGBA8, one texel per cell):
    R = primary   layer index, low 8 bits   (255 with no high bits = no tile)
    G = secondary layer index, low 8 bits   (255 with no high bits = no tile)
    B = dir1 | (primary   layer >> 8) << 3  stamp direction 0-7 in bits 0-2, index high bits above
    A = dir2 | (secondary layer >> 8) << 3

The stamp directions only ever use 3 bits, so the 5 spare bits of B/A carry the layer index's high
bits. That lifts the old 254-tile ceiling to 8191 WITHOUT a format break: in every atlas baked before
this, the high bits are zero and the file decodes exactly as it always did. Five zones need it —
freezone_a (458 tiles), eslantzone (440), battlezone (407), karus2004 (275), battlezone_e (271) —
and they are why those zones had no ground surface at all. Index 255 is reserved as the no-tile
sentinel and never assigned. The layer index reaches the shader through CUSTOM0 as an exact float
(Terrain.EmitVertex), so nothing downstream cares how many tiles a zone has.

Usage:
    python bake.py --only bake_terrain_ground                     # every zone with a GTD tile table
    python bake.py --only bake_terrain_ground --zones Moradon karus2004
    python bake.py --only bake_terrain_ground --verify Moradon    # re-bake in memory, diff vs what shipped
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from gtd_reader import read_gtd, tile_primary, tile_secondary, tile_dir1, tile_dir2
from kotools.gtt.gtt import read_gtt
from _paths import ko as _ko, assets as _assets  # noqa: E402

GTD_DIR = str(_ko() / "Zones")
DTEX_DIR = str(_ko() / "DTex")
OUT_DIR = _assets() / "terrain"


def _write_keep_import(p: Path) -> None:
    """Godot 'keep' import: ship verbatim so the client reads raw bytes via FileAccess."""
    (p.parent / (p.name + ".import")).write_text('[remap]\n\nimporter="keep"\n', encoding="utf-8")


def _resolve_dtex(rel_path: str, dtex_index: dict) -> str | None:
    """A tile path like 'dtex\\foo.gtt' -> real on-disk file (case-insensitive)."""
    base = rel_path.replace("/", "\\").split("\\")[-1].lower()
    return dtex_index.get(base)


def _ko_rgba(slot_idx, slots, paths, dtex_index, gtt_cache):
    """The original KO tile RGBA (native px) for a slot, or None if unresolved."""
    path_idx, chunk_idx = slots[slot_idx]
    real = _resolve_dtex(paths[path_idx], dtex_index)
    if real is None:
        return None
    if real not in gtt_cache:
        gtt_cache[real] = read_gtt(real)
    chunks = gtt_cache[real]
    if chunk_idx >= len(chunks):
        return None
    ch = chunks[chunk_idx]
    return bytes(getattr(ch, "rgba_data", None) or getattr(ch, "rgba"))


TILE_PX = 128                  # KO GTT chunks are native 128px — rendered verbatim
TILE_BYTES = TILE_PX * TILE_PX * 4
NO_TILE_BYTE = 255             # reserved sentinel; never assigned as a real index
MAX_TILES = 8192               # 13 bits: 8 low (R/G) + 5 high (B/A above the 3 direction bits)
MAGENTA = bytes([200, 60, 200, 255]) * (TILE_PX * TILE_PX)   # unresolvable tile marker


def zone_cells(zone: str):
    """Per-cell dense layer indices + stamp directions, and the slot each layer came from.

    Layers are numbered in first-seen order (primary then secondary, cell by cell) — the same walk
    a zone's layer numbering is stable across re-bakes. Index 255 is
    skipped so a no-tile cell stays unambiguous."""
    gtd = read_gtd(str(Path(GTD_DIR) / f"{zone}.gtd"))
    n = gtd["map_size"]
    slots, paths, cells = gtd["tile_slots"], gtd["tile_paths"], gtd["tiles"]
    if not slots or not paths:
        raise RuntimeError("no tile table (old GTD format or parse failed)")

    slot_to_layer: dict[int, int] = {}
    l1 = np.full(n * n, -1, dtype=np.int32)
    l2 = np.full(n * n, -1, dtype=np.int32)
    d1 = np.zeros(n * n, dtype=np.uint8)
    d2 = np.zeros(n * n, dtype=np.uint8)

    def layer_for(slot: int) -> int:
        if slot >= len(slots):
            return -1
        layer = slot_to_layer.get(slot)
        if layer is None:
            layer = len(slot_to_layer)
            if layer == NO_TILE_BYTE:          # reserve 255: it means "no tile" in the low byte
                layer += 1
            slot_to_layer[slot] = layer
        return layer

    for i, packed in enumerate(cells):
        l1[i] = layer_for(tile_primary(packed))
        sec = tile_secondary(packed)
        l2[i] = layer_for(sec) if sec != 1023 else -1
        d1[i] = tile_dir1(packed) & 7
        d2[i] = tile_dir2(packed) & 7

    layer_slot = {layer: slot for slot, layer in slot_to_layer.items()}
    count = max(layer_slot) + 1 if layer_slot else 0     # dense apart from the reserved 255
    return dict(n=n, l1=l1, l2=l2, d1=d1, d2=d2, layer_slot=layer_slot, count=count,
                slots=slots, paths=paths)


def build_atlas(zi: dict, dtex_index: dict, gtt_cache: dict) -> tuple[bytes, int]:
    """The zone's tiles as one layer-major RGBA strip. Returns (bytes, missing count)."""
    missing = 0
    parts: list[bytes] = []
    for layer in range(zi["count"]):
        slot = zi["layer_slot"].get(layer)
        ko = (_ko_rgba(slot, zi["slots"], zi["paths"], dtex_index, gtt_cache)
              if slot is not None else None)
        if ko is None:
            # A reserved/unresolvable layer: magenta keeps the strip dense so index == row block.
            if slot is not None:
                missing += 1
            parts.append(MAGENTA)
            continue
        parts.append(ko if len(ko) == TILE_BYTES else _resize(ko))
    return b"".join(parts), missing


def _resize(rgba: bytes) -> bytes:
    n = len(rgba) // 4
    src = int(round(n ** 0.5))
    if src * src * 4 != len(rgba):
        return (bytes(rgba) + b"\x00" * TILE_BYTES)[:TILE_BYTES]
    return Image.frombytes("RGBA", (src, src), bytes(rgba)).resize(
        (TILE_PX, TILE_PX), Image.LANCZOS).tobytes()


def build_splat(zi: dict) -> np.ndarray:
    """Per-cell RGBA8: low index bytes in R/G, direction + index high bits in B/A."""
    n = zi["n"]
    l1, l2 = zi["l1"], zi["l2"]
    sp = np.zeros((n * n, 4), dtype=np.uint8)
    sp[:, 0] = np.where(l1 < 0, NO_TILE_BYTE, l1 & 0xFF).astype(np.uint8)
    sp[:, 1] = np.where(l2 < 0, NO_TILE_BYTE, l2 & 0xFF).astype(np.uint8)
    sp[:, 2] = (zi["d1"] | np.where(l1 < 0, 0, (l1 >> 8) << 3).astype(np.uint8))
    sp[:, 3] = (zi["d2"] | np.where(l2 < 0, 0, (l2 >> 8) << 3).astype(np.uint8))
    return sp.reshape(n, n, 4)


def bake_zone(zone: str, dtex_index: dict, gtt_cache: dict, verify: bool) -> dict:
    zi = zone_cells(zone)
    if zi["count"] > MAX_TILES:
        raise RuntimeError(f"{zi['count']} tiles > {MAX_TILES} (13-bit splat index)")

    atlas, missing = build_atlas(zi, dtex_index, gtt_cache)
    splat = build_splat(zi)

    out = OUT_DIR / zone.lower()
    tiles_png, splat_png = out / "tiles.png", out / "splat.png"
    atlas_img = Image.frombytes("RGBA", (TILE_PX, TILE_PX * zi["count"]), atlas)
    splat_img = Image.fromarray(splat, "RGBA")

    if verify:
        # Compare the freshly-built PIXELS against what shipped (PNG bytes differ by encoder).
        def same(img, path):
            if not path.is_file():
                return "absent"
            have = np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)
            want = np.asarray(img, dtype=np.uint8)
            if have.shape != want.shape:
                return f"shape {have.shape} != {want.shape}"
            return "identical" if np.array_equal(have, want) else \
                   f"DIFFERS ({int((have != want).any(-1).sum())} texels)"
        return {"zone": zone, "n": zi["n"], "tiles": zi["count"], "missing": missing,
                "verify_tiles": same(atlas_img, tiles_png), "verify_splat": same(splat_img, splat_png)}

    out.mkdir(parents=True, exist_ok=True)
    atlas_img.save(tiles_png)
    splat_img.save(splat_png)
    # Ship both RAW: KoGroundTex reads them via FileAccess, so a normal texture import would
    # leave only a .ctex in the exported PCK and the ground would silently fall back to the
    # height tint. Every other terrain asset uses the same keep import.
    _write_keep_import(tiles_png)
    _write_keep_import(splat_png)
    return {"zone": zone, "n": zi["n"], "tiles": zi["count"], "missing": missing,
            "bytes": tiles_png.stat().st_size + splat_png.stat().st_size}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", nargs="+", default=None, help="zone names (default: all with a tile table)")
    ap.add_argument("--verify", nargs="+", default=None, help="re-bake in memory and diff vs the shipped files")
    args = ap.parse_args()

    dtex_index = {f.lower(): os.path.join(DTEX_DIR, f) for f in os.listdir(DTEX_DIR)}
    gtt_cache: dict = {}

    zones = args.verify or args.zones
    if not zones:
        terr = {p.name for p in OUT_DIR.iterdir() if p.is_dir() and (p / "heightmap.png").exists()}
        zones = sorted(z for z in {p.stem for p in Path(GTD_DIR).glob("*.gtd")} if z.lower() in terr)

    ok = skipped = 0
    for zone in zones:
        try:
            info = bake_zone(zone, dtex_index, gtt_cache, verify=bool(args.verify))
        except Exception as e:
            print(f"[ground] {zone}: SKIP ({type(e).__name__}: {e})", file=sys.stderr)
            skipped += 1
            continue
        if args.verify:
            print(f"[ground] {zone}: {info['n']}^2 tiles={info['tiles']} "
                  f"tiles.png={info['verify_tiles']} splat.png={info['verify_splat']}")
        else:
            print(f"[ground] {zone}: {info['n']}^2 tiles={info['tiles']}@{TILE_PX}px "
                  f"(missing={info['missing']}) -> {info['bytes'] // 1024} KB")
        ok += 1
    print(f"[ground] done: {ok} zone(s), {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

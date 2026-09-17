"""bake_collision.py -- One-time: extract a zone's RETAIL object-collision mesh from its .opd
and bake it to a compact binary the Godot client loads as one static collider.

KO does NOT mark collision per object. The .opd stores a single precomputed
collision TRIANGLE SOUP per map (right after the .opd header) — only the geometry the map designer
made solid is in it (so decorative bushes/trees that merely *look* like obstacles are absent). This
is the authoritative answer to "which objects block the player", replacing name/size guesses.

Reuses opd_convert's byte reader. Vertices are kept in KO world space (like placements.json); the
client converts via Terrain.KoToWorld at runtime, keeping Coord.cs the single KO<->Godot boundary.

Two header layouts exist and only the map dimensions tell them apart:
    legacy  [i32 nameLen][name (LCG 0x0816)][u32 compFlag][f32 w][f32 h][i32 faceCount]
    plain   [u32][i32 nameLen][name (ASCII)]              [f32 w][f32 h][i32 faceCount]

Output per zone: <output>/terrain/<zone>/collision.bin  (+ keep .import), little-endian:
    char[4] magic "KOCL"
    u32     triCount
    f32 * triCount*9   triangle vertices (v0,v1,v2) in KO space, row order = ConcavePolygonShape3D

Usage:
    python bake.py --only bake_collision --all           # every zone with both an .opd and a terrain dir
    python bake.py --only bake_collision --zones moradon elmo2004
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import opd_convert as O  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

OPD_DIR = str(_ko() / "Zones")
OUT_DIR = _assets() / "terrain"
ZONES_DEFAULT = ["moradon", "elmo2004"]
MAGIC = b"KOCL"
COORD_LIMIT = 1.0e6


def _try_header(data: bytes, plain: bool):
    r = O._R(data)
    if plain:
        r.u32()
    name_len = r.i32()
    if 0 < name_len <= 255:
        r.bytes(name_len)
    elif plain:
        return None
    if not plain:
        r.u32()
    map_w = r.f32(); map_h = r.f32()
    if not (0.0 < map_w <= 8192.0 and 0.0 < map_h <= 8192.0):
        return None
    face_count = r.i32()
    if not (0 <= face_count <= 1_000_000) or face_count * 36 > len(data) - r.p:
        return None
    return r, face_count


def extract_collision(opd_path: str) -> tuple[int, bytes]:
    """Return (triCount, raw f32 vertex bytes) of the .opd collision soup (KO space)."""
    data = Path(opd_path).read_bytes()
    hdr = _try_header(data, plain=False) or _try_header(data, plain=True)
    if hdr is None:
        raise ValueError("unrecognised .opd header (neither legacy nor plain-name layout)")
    r, face_count = hdr
    raw = bytes(r.bytes(face_count * 3 * 12))
    verts = struct.unpack(f"<{face_count * 9}f", raw)
    keep = bytearray()
    dropped = 0
    for i in range(face_count):
        tri = verts[i * 9:i * 9 + 9]
        if any(not math.isfinite(c) or abs(c) > COORD_LIMIT for c in tri):
            dropped += 1
            continue
        keep += raw[i * 36:i * 36 + 36]
    return face_count - dropped, bytes(keep), dropped


def _write_keep_import(p: Path) -> None:
    (p.parent / (p.name + ".import")).write_text('[remap]\n\nimporter="keep"\n', encoding="utf-8")


def bake_zone(zone: str, out_dir: Path) -> dict | None:
    tris, vbytes, dropped = extract_collision(str(Path(OPD_DIR) / f"{zone}.opd"))
    if tris == 0:
        return None
    zone_dir = out_dir / zone.lower()
    zone_dir.mkdir(parents=True, exist_ok=True)
    dst = zone_dir / "collision.bin"
    with open(dst, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", tris))
        f.write(vbytes)
    _write_keep_import(dst)
    return {"zone": zone.lower(), "tris": tris, "dropped": dropped,
            "bytes": dst.stat().st_size, "path": str(dst)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake KO .opd object-collision mesh -> collision.bin.")
    ap.add_argument("--zones", nargs="+", default=ZONES_DEFAULT, metavar="NAME")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out)
    zones = args.zones
    if args.all:
        have = {d.name.lower() for d in out_dir.iterdir() if d.is_dir()}
        zones = sorted(p.stem for p in Path(OPD_DIR).glob("*.opd") if p.stem.lower() in have)

    ok = 0
    for zone in zones:
        try:
            info = bake_zone(zone, out_dir)
        except Exception as e:
            print(f"[collision] {zone}: {e}", file=sys.stderr)
            continue
        if info is None:
            print(f"[collision] {zone}: no collision faces")
            continue
        drop = f"  (dropped {info['dropped']} degenerate)" if info["dropped"] else ""
        print(f"[collision] {info['zone']}: {info['tris']} tris -> {info['bytes']} bytes{drop}")
        ok += 1
    print(f"[collision] done ({ok}/{len(zones)}) -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

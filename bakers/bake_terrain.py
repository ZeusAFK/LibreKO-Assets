"""bake_terrain.py -- One-time: decode KO client .gtd terrain into a compact
heightfield the Godot client bakes into chunk meshes.

Step 1 of the terrain bake (mirrors bake_tables.py). Reuses the verified
gtd_reader; emits a small binary .hmap per zone. The .gtt textures are a
separate, later step (slot->path tail still unresolved), so this is heights only.

Output per zone (<output>/maps/<zone>/terrain.hmap), little-endian:
    char[4] magic  "KHMF"            (KO HeightMap Field)
    i32     map_size                 (cells per side, e.g. 257)
    f32     cell_size                (world units per cell = 4.0)
    f32     min_height
    f32     max_height
    f32 * (map_size*map_size) heights, row-major z-outer/x-inner:
        index = z*map_size + x  -> world (x*cell_size, height, z*cell_size)

Usage:
    python bake.py --only bake_terrain --data "<retail install>\\Zones"
    python bake.py --only bake_terrain --data DIR --zones Moradon elmo2004
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from gtd_reader import read_gtd  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

CELL_SIZE = 4.0   # terrain unit distance is a hardcoded 4.0
MAGIC = b"KHMF"

# Zones the client currently spawns in (server ZoneInfos: 2->elmo2004, 21->Moradon).
DEFAULT_ZONES = ["Moradon", "elmo2004"]

# Output is a Godot-native heightmap.png (RGBA8 where each pixel's 4 bytes ARE the
# little-endian IEEE-754 f32 height) under assets/terrain/<zone>/, read at runtime
# via FileAccess + Image.LoadPngFromBuffer. A sibling .import (importer="keep") ships
# the file verbatim. No custom binary, no C# re-conversion step — straight from KO.
OUT_DIR = _assets() / "terrain"


def _write_keep_import(p: Path) -> None:
    """Godot 'keep' import: ship the file verbatim (no texture conversion) so the
    client reads its raw bytes via FileAccess."""
    (p.parent / (p.name + ".import")).write_text(
        '[remap]\n\nimporter="keep"\n', encoding="utf-8")


def bake_zone(gtd_path: Path, out_dir: Path) -> dict | None:
    t = read_gtd(gtd_path)
    n = t["map_size"]
    rows = t["heights"]                      # rows[z][x]

    flat = np.zeros(n * n, dtype="<f4")
    for z in range(n):
        row = rows[z]
        base = z * n
        for x in range(n):
            h = row[x]
            flat[base + x] = h if math.isfinite(h) else 0.0
    mn = float(flat.min()) if flat.size else 0.0
    mx = float(flat.max()) if flat.size else 0.0

    # n*n f32 -> n*n RGBA8: pixel (row z, col x) = the float's 4 LE bytes. Godot
    # reads them back exactly (PNG lossless; no compression/sRGB on raw RGBA8).
    px = flat.view(np.uint8).reshape(n, n, 4)
    map_dir = out_dir / gtd_path.stem.lower()    # assets/terrain/<name>/
    map_dir.mkdir(parents=True, exist_ok=True)
    dst = map_dir / "heightmap.png"
    Image.fromarray(px, "RGBA").save(dst)
    _write_keep_import(dst)

    # Self-check: re-decode and assert byte-exact (catches any encoder surprise).
    back = np.asarray(Image.open(dst), dtype=np.uint8).reshape(-1).view("<f4")
    if back.shape[0] != n * n or not np.array_equal(back, flat):
        raise RuntimeError(f"{gtd_path.stem}: heightmap.png round-trip mismatch")
    checksum = int(flat.view(np.uint32).astype(np.uint64).sum()) & 0xFFFFFFFF

    return {
        "zone": gtd_path.stem.lower(), "map_size": n, "cell_size": CELL_SIZE,
        "extent": (n - 1) * CELL_SIZE, "min": mn, "max": mx,
        "checksum": checksum, "bytes": dst.stat().st_size, "path": str(dst),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode KO .gtd -> .hmap heightfield.")
    ap.add_argument("--data", help="KO Zones dir containing *.gtd", default=str(_ko() / "Zones"))
    ap.add_argument("--zones", nargs="+", default=DEFAULT_ZONES, metavar="NAME")
    ap.add_argument("--all", action="store_true", help="every zone with a .gtd in the data dir")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    data_dir = Path(args.data)
    out_dir = Path(args.out)
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    zones = sorted(p.stem for p in data_dir.glob("*.gtd")) if args.all else args.zones
    ok = 0
    for zone in zones:
        src = data_dir / f"{zone}.gtd"
        if not src.exists():
            matches = [p for p in data_dir.glob("*.gtd") if p.stem.lower() == zone.lower()]
            if not matches:
                print(f"[terrain] MISSING {zone}.gtd", file=sys.stderr)
                continue
            src = matches[0]
        try:
            info = bake_zone(src, out_dir)
        except Exception as e:
            print(f"[terrain] {zone}: {e}", file=sys.stderr)
            continue
        print(f"[terrain] {info['zone']}: {info['map_size']}^2 "
              f"extent={info['extent']:.0f}u h=[{info['min']:.1f},{info['max']:.1f}] "
              f"-> {info['bytes']} bytes")
        ok += 1

    print(f"[terrain] done ({ok}/{len(zones)}) -> {out_dir}")
    return 0 if ok and (args.all or ok == len(zones)) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""bake_water.py -- One-time: decode KO client .gtd water/river grids into a
Godot-native per-zone water.json the runtime turns into animated water meshes.

Mirrors bake_terrain.py / bake_objects.py: reads straight from the KO .gtd via the
verified water_extract, emits a small JSON per zone next to that zone's terrain
assets (assets/terrain/<zone>/water.json), and the runtime (src/Terrain/Water.cs)
reads it via FileAccess -- no .gtd / cipher at run time.

Coordinates are kept in KO world space (x east, y up, z north), exactly like
placements.json: the client applies the handedness flip + terrain orientation through
Terrain.KoToWorld at runtime, so Coord.cs stays the single KO<->Godot boundary and
re-tuning the terrain never needs a re-bake. (Validated: the river vertices share the
server's koX/koZ frame -- identity mapping puts every patch on its riverbed.)

Each patch's flat surface Y already lives in the vertices; the engine adds a small
heightOffset at load (default 0.2 when 0 stored), which we fold into
the baked Y so the runtime is dumb.

Output per zone (<output>/terrain/<zone>/water.json):
    [ { "tex": "ka_water", "cols": C, "rows": R, "level_y": Y,
        "verts": [[x,y,z], ...]  # KO space, row-major (r*cols + c), len == cols*rows
      }, ... ]

Usage:
    python bake.py --only bake_water                       # all *.gtd in the default Zones dir
    python bake.py --only bake_water --zones moradon elmo2004
    python bake.py --only bake_water --data "<retail install>\\Zones"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from water_extract import extract_rivers, DEFAULT_HEIGHT_OFFSET  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

# Same KO install as bake_terrain.py / bake_mobs.py.
DEFAULT_DATA = str(_ko() / "Zones")

# water.json sits alongside the zone's heightmap.png / placements.json so it ships in
# the same terrain pack and the runtime finds it by the same <stem> key.
OUT_DIR = _assets() / "terrain"


def _write_keep_import(p: Path) -> None:
    """Godot 'keep' import: ship the file verbatim so the client reads its raw bytes via
    FileAccess (same sidecar heightmap.png / placements.json use)."""
    (p.parent / (p.name + ".import")).write_text(
        '[remap]\n\nimporter="keep"\n', encoding="utf-8")


def tex_key(texture: str) -> str:
    """Texture file name -> a stable key (basename, no extension, lowercased). The
    runtime treats this as a MATERIAL key (river vs still water), never a file."""
    name = texture.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".dxt"):
        name = name[:-4]
    return name.lower()


def bake_zone(gtd_path: Path, out_dir: Path) -> dict | None:
    rivers = extract_rivers(str(gtd_path))
    if not rivers:
        return None

    patches = []
    total_verts = 0
    for r in rivers:
        off = r["height_offset"] or DEFAULT_HEIGHT_OFFSET   # 0 stored -> engine default 0.2
        verts = [[x, y + off, z] for (x, y, z) in r["vertices"]]
        level_y = r["water_level_y"][0] + off
        patches.append({
            "tex": tex_key(r["texture"]),
            "cols": r["cols"],
            "rows": r["rows"],
            "level_y": round(level_y, 4),
            "verts": [[round(c, 3) for c in v] for v in verts],
        })
        total_verts += len(verts)

    zone = gtd_path.stem.lower()
    zone_dir = out_dir / zone
    zone_dir.mkdir(parents=True, exist_ok=True)
    dst = zone_dir / "water.json"
    dst.write_text(json.dumps(patches, separators=(",", ":")), encoding="utf-8")
    _write_keep_import(dst)

    return {"zone": zone, "patches": len(patches), "verts": total_verts,
            "bytes": dst.stat().st_size, "path": str(dst)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode KO .gtd water grids -> per-zone water.json.")
    ap.add_argument("--data", default=DEFAULT_DATA, help="KO Zones dir containing *.gtd")
    ap.add_argument("--zones", nargs="*", metavar="NAME",
                    help="zone stems to bake (default: every *.gtd in --data)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    data_dir = Path(args.data)
    out_dir = Path(args.out)
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    if args.zones:
        srcs = []
        for zone in args.zones:
            src = data_dir / f"{zone}.gtd"
            if not src.exists():
                matches = [p for p in data_dir.glob("*.gtd") if p.stem.lower() == zone.lower()]
                if not matches:
                    print(f"[water] MISSING {zone}.gtd", file=sys.stderr)
                    continue
                src = matches[0]
            srcs.append(src)
    else:
        srcs = sorted(data_dir.glob("*.gtd"))

    baked = 0
    dry = 0
    for src in srcs:
        try:
            info = bake_zone(src, out_dir)
        except Exception as e:
            print(f"[water] {src.stem}: {e}", file=sys.stderr)
            continue
        if info is None:
            dry += 1
            continue
        print(f"[water] {info['zone']}: {info['patches']} patch(es), "
              f"{info['verts']} verts -> {info['bytes']} bytes")
        baked += 1

    print(f"[water] done: {baked} zone(s) with water, {dry} without -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

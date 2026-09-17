"""convert_assets.py -- Batch N3 asset conversion driver built on kotools.

Modes:
  inventory (default)  Scan a game dir, classify/count assets, write asset_inventory.json
  convert              Batch-convert: .n3chr->.gltf, .dxt->.png, .gtt->.png (8 tiles each)

Usage:
  python convert_assets.py inventory --game-dir DATA_DIR --out out/
  python convert_assets.py convert   --game-dir DATA_DIR --out out/converted --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))   # vendored kotools

# Asset extensions to classify
ASSET_EXTENSIONS = {
    ".n3chr":    "character",
    ".n3pmesh":  "pmesh",
    ".n3vmesh":  "vmesh",
    ".n3shape":  "shape",
    ".n3cpart":  "cpart",
    ".n3cplug":  "cplug",
    ".dxt":      "texture_dxt",
    ".gtt":      "texture_gtt",
    ".n3anim":   "animation",
    ".n3joint":  "skeleton",
    ".n3cskins": "skin",
}


# ---------------------------------------------------------------------------
# Inventory mode
# ---------------------------------------------------------------------------
def cmd_inventory(game_dir: Path, out_dir: Path) -> None:
    """Scan game dir for N3 assets; write asset_inventory.json."""
    print(f"Scanning: {game_dir}")

    counts: dict[str, int] = {v: 0 for v in ASSET_EXTENSIONS.values()}
    by_type: dict[str, list[str]] = {v: [] for v in ASSET_EXTENSIONS.values()}

    total = 0
    for p in game_dir.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in ASSET_EXTENSIONS:
            kind = ASSET_EXTENSIONS[ext]
            counts[kind] += 1
            by_type[kind].append(str(p.relative_to(game_dir)))
            total += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    inv_path = out_dir / "asset_inventory.json"
    inv = {
        "game_dir": str(game_dir),
        "total_assets": total,
        "counts": counts,
        "files": by_type,
    }
    inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nAsset inventory ({total} total):")
    for kind, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        if cnt:
            print(f"  {kind:<20} {cnt:>6}")
    print(f"\nWrote: {inv_path}")


# ---------------------------------------------------------------------------
# Convert mode — .n3chr -> glTF
# ---------------------------------------------------------------------------
def convert_chr(src: Path, out_dir: Path, game_dir: Path) -> Path:
    """Convert one .n3chr to .gltf using kotools."""
    from kotools.character.chr import read_chr
    from kotools.character.cpart import N3CPart
    from kotools.character.cskins import N3CPartSkins
    from kotools.skeleton.joint import read_joint
    from kotools.animation.animcontrol import read_anim
    from kotools.ntf.texture import read_dxt
    from kotools.mesh.skin import N3Skin
    from kotools.exchange.gltf_export import export_character_to_gltf
    from kotools.io.binary_reader import BinaryReader

    chr_data = read_chr(str(src))

    # Mirror input subpath under out_dir
    try:
        rel = src.relative_to(game_dir)
    except ValueError:
        rel = Path(src.name)
    out_path = out_dir / rel.with_suffix(".gltf")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Skeleton
    joint_path = game_dir / chr_data.joint_path.replace("\\", "/")
    joint = read_joint(str(joint_path))

    # Animation
    anim_ctrl = None
    if chr_data.anim_ctrl_path:
        anim_path = game_dir / chr_data.anim_ctrl_path.replace("\\", "/")
        if anim_path.exists():
            try:
                anim_ctrl = read_anim(str(anim_path))
            except Exception:
                anim_ctrl = None

    # Parts
    skins = []
    textures = []
    for part_path_str in chr_data.part_paths:
        pp = game_dir / part_path_str.replace("\\", "/")
        if not pp.exists():
            continue
        with open(str(pp), "rb") as f:
            part = N3CPart.from_reader(BinaryReader(f.read()))

        tex = None
        if part.texture_path:
            tp = game_dir / part.texture_path.replace("\\", "/")
            if tp.exists():
                try:
                    tex = read_dxt(str(tp))
                except Exception:
                    pass
        textures.append(tex)

        if part.skins_path:
            sp = game_dir / part.skins_path.replace("\\", "/")
            if sp.exists():
                try:
                    with open(str(sp), "rb") as f:
                        cskins = N3CPartSkins.from_reader(BinaryReader(f.read()))
                    highest = cskins.highest_lod
                    if highest:
                        skins.append(highest)
                    else:
                        from kotools.mesh.imesh import N3IMesh
                        skins.append(N3Skin(mesh=N3IMesh(name="")))
                except Exception:
                    pass

    export_character_to_gltf(joint, skins, textures, anim_ctrl, str(out_path), chr_data.name)
    return out_path


# ---------------------------------------------------------------------------
# Convert mode — .dxt -> PNG
# ---------------------------------------------------------------------------
def convert_dxt(src: Path, out_dir: Path, game_dir: Path) -> Path:
    """Convert one .dxt texture to PNG using kotools."""
    from kotools.ntf.texture import read_dxt

    tex = read_dxt(str(src))
    try:
        rel = src.relative_to(game_dir)
    except ValueError:
        rel = Path(src.name)
    out_path = out_dir / rel.with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tex.save_png(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Convert mode — .gtt -> PNG (8 tiles each)
# ---------------------------------------------------------------------------
def convert_gtt(src: Path, out_dir: Path, game_dir: Path) -> list[Path]:
    """Convert one .gtt (8 tiles) to PNG files."""
    from kotools.gtt.gtt import read_gtt

    textures = read_gtt(str(src))
    try:
        rel = src.relative_to(game_dir)
    except ValueError:
        rel = Path(src.name)

    out_paths = []
    for i, tex in enumerate(textures):
        out_path = out_dir / rel.parent / f"{src.stem}_{i}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tex.save_png(out_path)
        out_paths.append(out_path)
    return out_paths


# ---------------------------------------------------------------------------
# Batch convert
# ---------------------------------------------------------------------------
def cmd_convert(
    game_dir: Path,
    out_dir: Path,
    limit: int | None,
    types: list[str] | None,
) -> None:
    """Batch-convert N3 assets to modern formats."""
    if types is None:
        types = ["chr", "dxt", "gtt"]

    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"ok": [], "error": []}

    def _run(label: str, src: Path, fn, *args):
        try:
            out = fn(src, out_dir, game_dir, *args)
            if isinstance(out, list):
                for o in out:
                    results["ok"].append({"src": str(src), "out": str(o)})
                    print(f"  OK  {src.name} -> {o.name}  ({o.stat().st_size:,} B)")
            else:
                results["ok"].append({"src": str(src), "out": str(out)})
                print(f"  OK  {src.name} -> {out.name}  ({out.stat().st_size:,} B)")
        except Exception as e:
            results["error"].append({"src": str(src), "error": str(e)})
            print(f"  ERR {src.name}: {e}")

    n_done = 0

    if "chr" in types:
        print("\n--- Characters (.n3chr -> .gltf) ---")
        for p in sorted(game_dir.rglob("*.n3chr")):
            if limit and n_done >= limit:
                break
            _run("chr", p, convert_chr)
            n_done += 1

    if "dxt" in types:
        print("\n--- Textures (.dxt -> .png) ---")
        for p in sorted(game_dir.rglob("*.dxt")):
            if limit and n_done >= limit:
                break
            _run("dxt", p, convert_dxt)
            n_done += 1

    if "gtt" in types:
        print("\n--- Ground tiles (.gtt -> .png) ---")
        for p in sorted(game_dir.rglob("*.gtt")):
            if limit and n_done >= limit:
                break
            _run("gtt", p, convert_gtt)
            n_done += 1

    summary_path = out_dir / "convert_summary.json"
    summary_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone: {len(results['ok'])} OK, {len(results['error'])} errors")
    print(f"Summary: {summary_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Batch N3 -> glTF/PNG asset conversion using kotools."
    )
    sub = parser.add_subparsers(dest="mode")

    p_inv = sub.add_parser("inventory", help="Scan and classify assets")
    p_inv.add_argument("--game-dir", default=str(_ko()))
    p_inv.add_argument("--out", default="out")

    p_conv = sub.add_parser("convert", help="Batch convert assets")
    p_conv.add_argument("--game-dir", default=str(_ko()))
    p_conv.add_argument("--out", default=str(
        _assets() / "converted"
    ))
    p_conv.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Stop after N files (smoke test)")
    p_conv.add_argument("--types", nargs="+", choices=["chr", "dxt", "gtt"],
                        default=None, help="Asset types to convert")

    args = parser.parse_args()

    mode = args.mode or "inventory"
    game_dir = Path(getattr(args, "game_dir", str(_ko())))
    out_dir = Path(getattr(args, "out", "out"))

    if not game_dir.exists():
        print(f"ERROR: game-dir not found: {game_dir}", file=sys.stderr)
        sys.exit(1)

    if mode == "inventory":
        cmd_inventory(game_dir, out_dir)
    elif mode == "convert":
        cmd_convert(game_dir, out_dir, args.limit, args.types)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

r"""bake_target_symbol.py -- One-time: bake retail's ground target-selection symbols.

The green segmented ring drawn under a selected target is a real KO asset, not something to
approximate procedurally: `fx\ground\target_symbol\target_symbol_plane_%d.dxt`, packed inside
`fx/fx.hdr` + `fx/fx.src` (the on-disk `fx/ground/target_symbol/` folder ships empty).

Five planes, 256x256 DXT1, drawn ADDITIVELY on the ground -- their alpha is fully opaque and the
surround is black, so anything but an additive blend shows a black square.

    0  the plain segmented ring (what retail draws under a normal target)
    1  swirl + dashed inner ring
    2  six-pointed star inside a disc
    3  El Morad variant
    4  Karus variant

Output: <output>/ui/target/target_symbol_plane_<n>.png

Usage:
    python bake.py --only bake_target_symbol

Godot only writes .import sidecars from the editor, so after a re-bake run:
    Godot_v4.7.2-stable_mono_win64_console.exe --headless --path client --import
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

DEFAULT_HDR = _ko() / "fx" / "fx.hdr"
OUT_DIR = _assets() / "ui" / "target"
PLANES = 5


def bake(hdr: Path, out_dir: Path) -> tuple[int, list[str]]:
    from PIL import Image

    from fxb_parse import extract_from_archive
    from ko_texture import decode_ntf

    out_dir.mkdir(parents=True, exist_ok=True)
    baked, failed = 0, []
    for plane in range(PLANES):
        entry = "ground\\target_symbol\\target_symbol_plane_%d.dxt" % plane
        try:
            decoded = decode_ntf(extract_from_archive(str(hdr), entry))
        except KeyError:
            failed.append(entry)
            continue
        if decoded is None:
            failed.append(entry)
            continue
        width, height, rgba = decoded
        Image.frombytes("RGBA", (width, height), rgba.tobytes()).convert("RGB").save(
            out_dir / f"target_symbol_plane_{plane}.png", "PNG", optimize=True)
        baked += 1
    return baked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake KO ground target-selection symbols.")
    parser.add_argument("--hdr", default=str(DEFAULT_HDR))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    baked, failed = bake(Path(args.hdr), Path(args.out))
    print(f"[target] {baked}/{PLANES} planes -> {args.out}")
    for entry in failed:
        print(f"[target] missing: {entry}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

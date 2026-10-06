r"""bake_weapon_trail.py -- bake the weapon swing-trail textures.

Retail loads five trace textures from `Item/item.hdr` + `Item/item.src` into every weapon plug and draws
the one matching the weapon's element (stretched once along the ribbon; alpha gives the head/tail fade).

Output: <output>/fx/tex/weapon_trail.png and weapon_trail_<fire|ice|lightning|poison>.png

Usage:
    python bake.py --only weapon-trail
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_HDR = _ko() / "Item" / "item.hdr"
OUT_DIR = _assets() / "fx" / "tex"
TRAILS = (
    ("default_normal_trace.dxt", "weapon_trail.png"),
    ("default_fire_trace.dxt", "weapon_trail_fire.png"),
    ("default_ice_trace.dxt", "weapon_trail_ice.png"),
    ("default_lightting_trace.dxt", "weapon_trail_lightning.png"),
    ("default_poison_trace.dxt", "weapon_trail_poison.png"),
)


def bake(hdr: Path, out_dir: Path) -> tuple[list[Path], list[str]]:
    from PIL import Image

    from fxb_parse import extract_from_archive
    from ko_texture import decode_ntf

    out_dir.mkdir(parents=True, exist_ok=True)
    baked, failed = [], []
    for entry, name in TRAILS:
        try:
            decoded = decode_ntf(extract_from_archive(str(hdr), entry))
        except KeyError:
            failed.append(f"{entry} is not in {hdr}")
            continue
        if decoded is None:
            failed.append(f"{entry} is in a texture format that cannot be decoded")
            continue
        width, height, rgba = decoded
        out = out_dir / name
        Image.frombytes("RGBA", (width, height), rgba.tobytes()).save(out, "PNG", optimize=True)
        baked.append(out)
    return baked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake the KO weapon swing-trail textures.")
    parser.add_argument("--hdr", default=str(DEFAULT_HDR))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    try:
        baked, failed = bake(Path(args.hdr), Path(args.out))
    except OSError as exc:
        print(f"[weapon-trail] {exc}", file=sys.stderr)
        return 1
    print(f"[weapon-trail] {len(baked)}/{len(TRAILS)} trail textures -> {args.out}")
    for problem in failed:
        print(f"[weapon-trail] {problem}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

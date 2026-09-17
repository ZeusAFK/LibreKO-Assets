r"""bake_warps.py -- One-time: bake the gatekeeper/warp-gate destination catalogue.

Retail keeps the *presentation* half of a warp menu on the CLIENT: the server's
GS_WARP_LIST only carries (warpId, name, announce, zoneId, maxUsers, fee), and the
retail client throws the server's name away and looks warpId up in Data\WarpInfo.tbl
for the display name, level range, zone painting and description (the warp UI).
A warp id with no WarpInfo row is dropped from the menu entirely -- reproduced here by
simply not baking a row for it.

Sources:
    Data\warpinfo.tbl   id, name, minLevel, maxLevel, "npcimg\<x>.dxt", description
    npcimg\*.dxt        256x256 NTF zone paintings (v7 -> RC4 per scanline)

Output:
    <output>/ui/warp/warps.json   {"<id>": {n, lo, hi, img, d}}
    <output>/ui/warp/<img>.png    one painting per referenced image

Usage:
    python bake.py --only bake_warps
    python bake.py --only bake_warps --data str(_ko())

Godot only writes .import sidecars from the editor, so after baking run:
    Godot_v4.7.2-stable_mono_win64_console.exe --headless --path client --import
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

DEFAULT_DATA = _ko()
OUT_DIR = _assets() / "ui" / "warp"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("`", "'")).strip()


def _img_stem(path: str) -> str:
    return Path((path or "").replace("\\", "/")).stem.lower()


PADDING_THRESHOLD = 12


def _content_box(painting):
    """The paintings are authored ~163x187 in the top-left of a 256x256 texture; retail's img_zone
    samples that sub-rect and the rest is black padding, so crop it off rather than ship the gap."""
    from PIL import Image

    box = painting.point(lambda v: 255 if v > PADDING_THRESHOLD else 0).convert("L").getbbox()
    return box or (0, 0, painting.width, painting.height)


def bake(root: Path, out_dir: Path) -> tuple[int, int, list[str]]:
    from PIL import Image

    from ko_texture import decode_ntf
    from kotools.tbl.reader import read_tbl

    table = read_tbl(str(root / "Data" / "warpinfo.tbl"))
    out_dir.mkdir(parents=True, exist_ok=True)

    catalogue: dict[str, dict] = {}
    wanted: dict[str, str] = {}
    for warp_id, name, min_level, max_level, image, description in table.rows:
        name = _clean(name)
        if not name:
            continue
        stem = _img_stem(image)
        catalogue[str(int(warp_id))] = {
            "n": name,
            "lo": int(min_level),
            "hi": int(max_level),
            "img": stem,
            "d": _clean(description),
        }
        if stem:
            wanted.setdefault(stem, image.replace("\\", "/"))

    (out_dir / "warps.json").write_text(
        json.dumps(catalogue, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    (out_dir / "warps.json.import").write_text(
        '[remap]\n\nimporter="keep"\n', encoding="utf-8")

    missing: list[str] = []
    baked = 0
    for stem, rel in sorted(wanted.items()):
        src = root / rel
        if not src.is_file():
            candidates = list((root / "npcimg").glob(f"{Path(rel).stem}.*"))
            if not candidates:
                missing.append(rel)
                continue
            src = candidates[0]
        decoded = decode_ntf(src.read_bytes())
        if decoded is None:
            missing.append(rel)
            continue
        width, height, rgba = decoded
        painting = Image.frombytes("RGBA", (width, height), rgba.tobytes()).convert("RGB")
        painting.crop(_content_box(painting)).save(out_dir / f"{stem}.png", "PNG", optimize=True)
        baked += 1

    return len(catalogue), baked, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake WarpInfo.tbl + npcimg paintings.")
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="retail client root")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    rows, images, missing = bake(Path(args.data), Path(args.out))
    print(f"[warp] {rows} destinations, {images} paintings -> {args.out}")
    for rel in missing:
        print(f"[warp] missing image: {rel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

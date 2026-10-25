"""bake_durability.py -- the equipment wear figure art.

The wear figure (ui\\co_durability.uif) draws a body outline with one worn image per gear part, an animated
image whose frames ping-pong over five pictures, and one broken image per part, all cut from
ui\\co_durability.dxt. The cells below are that layout's: each piece is its crop box in the sheet
(left, top, right, bottom) and its place in the 97x79 figure (x, y, width, height). Parts 0-7 are the
left hand, right hand, shield, feet, legs, chest, gloves and head; the three hand parts carry the
layout's tooltip.

Output: <output>/ui/durability/ (base.png, worn_<part>_<frame>.png, broken_<part>.png, layout.json)

Usage:
    python bake.py --only durability
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_items  # noqa: E402
from fxb_parse import _hdr_index  # noqa: E402
from _paths import assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "ui" / "durability"
SHEET = "co_durability.dxt"
SIZE = (97, 79)
FPS = 6.0
BASE = ((35, 10, 92, 88), (21, 0, 57, 78))
WEAPON_PARTS = 3
WEAPON_TOOLTIP = "When durability reaches 0, using skill consumes additional mana"
PARTS = (
    (
        (((442, 4, 473, 37), (65, 26, 31, 33)),
        ((443, 49, 474, 82), (65, 26, 31, 33)),
        ((443, 93, 474, 126), (65, 26, 31, 33)),
        ((444, 141, 475, 174), (65, 26, 31, 33)),
        ((445, 188, 476, 221), (65, 26, 31, 33))),
        ((445, 188, 476, 221), (65, 26, 31, 33)),
    ),
    (
        (((388, 9, 421, 40), (0, 26, 33, 31)),
        ((389, 49, 422, 80), (0, 26, 33, 31)),
        ((389, 93, 422, 124), (0, 26, 33, 31)),
        ((388, 145, 421, 176), (0, 26, 33, 31)),
        ((389, 189, 422, 220), (0, 26, 33, 31))),
        ((389, 189, 422, 220), (0, 26, 33, 31)),
    ),
    (
        (((360, 11, 380, 42), (66, 39, 20, 31)),
        ((361, 95, 381, 126), (66, 39, 20, 31)),
        ((361, 50, 381, 81), (66, 39, 20, 31)),
        ((361, 147, 381, 178), (66, 39, 20, 31)),
        ((362, 190, 382, 221), (66, 39, 20, 31))),
        ((362, 190, 382, 221), (66, 39, 20, 31)),
    ),
    (
        (((301, 16, 352, 34), (21, 60, 51, 18)),
        ((301, 55, 352, 73), (21, 60, 51, 18)),
        ((302, 99, 353, 117), (21, 60, 51, 18)),
        ((301, 152, 352, 170), (21, 60, 51, 18)),
        ((302, 195, 353, 213), (21, 60, 51, 18))),
        ((302, 195, 353, 213), (21, 60, 51, 18)),
    ),
    (
        (((264, 16, 291, 35), (35, 47, 27, 19)),
        ((265, 55, 292, 74), (35, 46, 27, 19)),
        ((265, 99, 292, 118), (35, 47, 27, 19)),
        ((265, 151, 292, 170), (35, 47, 27, 19)),
        ((265, 195, 292, 214), (35, 47, 27, 19))),
        ((265, 195, 292, 214), (35, 47, 27, 19)),
    ),
    (
        (((221, 13, 250, 36), (36, 27, 29, 23)),
        ((221, 53, 250, 76), (36, 27, 29, 23)),
        ((222, 97, 251, 120), (36, 27, 29, 23)),
        ((221, 149, 250, 172), (36, 27, 29, 23)),
        ((222, 193, 251, 216), (36, 27, 29, 23))),
        ((222, 193, 251, 216), (36, 27, 29, 23)),
    ),
    (
        (((156, 6, 211, 45), (23, 19, 55, 39)),
        ((156, 46, 211, 85), (23, 20, 55, 39)),
        ((157, 90, 212, 129), (23, 19, 55, 39)),
        ((156, 142, 211, 181), (23, 20, 55, 39)),
        ((157, 185, 212, 224), (23, 19, 55, 39))),
        ((157, 185, 212, 224), (23, 19, 55, 39)),
    ),
    (
        (((119, 10, 140, 38), (38, 0, 21, 28)),
        ((120, 50, 141, 78), (38, 0, 21, 28)),
        ((120, 94, 141, 122), (38, 0, 21, 28)),
        ((119, 146, 140, 174), (38, 0, 21, 28)),
        ((120, 190, 141, 218), (38, 0, 21, 28))),
        ((120, 190, 141, 218), (38, 0, 21, 28)),
    ),
)


def base_name(path: str) -> str:
    return path.lower().replace("\\", "/").rsplit("/", 1)[-1]


def load_sheet(idx, src, name):
    real = next((k for k in idx if base_name(k) == name), None)
    if real is None:
        raise SystemExit(f"[durability] not in archive: {name}")
    offset, size = idx[real]
    src.seek(offset)
    return bake_items.decode_icon(src.read(size)).convert("RGBA")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", type=Path, default=bake_items.KO_ROOT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    hdr, src_path = args.ko / "UI" / "ui.hdr", args.ko / "UI" / "ui.src"
    if not hdr.exists() or not src_path.exists():
        print(f"[durability] missing archive: {hdr} / {src_path}", file=sys.stderr)
        return 1

    bake_items._ensure_icon_deps()
    args.out.mkdir(parents=True, exist_ok=True)
    idx = _hdr_index(str(hdr))
    with open(src_path, "rb") as src:
        sheet = load_sheet(idx, src, SHEET)

    base_box, base_rect = BASE
    sheet.crop(base_box).save(args.out / "base.png")
    layout = {"size": list(SIZE), "base": list(base_rect), "fps": FPS, "parts": []}
    for part, (worn, broken) in enumerate(PARTS):
        frames = []
        for frame, (box, rect) in enumerate(worn):
            sheet.crop(box).save(args.out / f"worn_{part}_{frame}.png")
            frames.append(list(rect))
        broken_box, broken_rect = broken
        sheet.crop(broken_box).save(args.out / f"broken_{part}.png")
        tooltip = WEAPON_TOOLTIP if part < WEAPON_PARTS else ""
        layout["parts"].append({"worn": frames, "broken": list(broken_rect), "tooltip": tooltip})
    (args.out / "layout.json").write_text(json.dumps(layout, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[durability] base, {len(PARTS)} parts x {len(PARTS[0][0])} worn frames and {len(PARTS)} broken -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

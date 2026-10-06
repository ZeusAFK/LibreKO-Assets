"""bake_fortune.py -- bake Mekin's tarot reading: fortune_us.tbl and the card art from UI\\ui.hdr/.src.

fortune_us.tbl columns: id, category (card 0-19, card 7 has no art and no rows), level (stars 1-5), title,
three lines joined by a backslash, weight of the random draw. The card faces are UI\\cardimg\\cardNN.dxt, the
face-down card is a region of UI\\co_board.dxt (normal / hover, as co_fortune_us.uif draws btn_cardN), and the
turn and reveal animations are UI\\cardimg\\fx_taro_center_spin_* and fx_taro_final_*.

    python bake.py --only bake_fortune

Writes <output>/ui/fortune/fortune.json and its PNGs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

import bake_items  # noqa: E402
from fxb_parse import _hdr_index  # noqa: E402

DEFAULT_TBL = _ko() / "Data" / "fortune_us.tbl"
OUT = _assets() / "ui" / "fortune"

ID, CATEGORY, LEVEL, TITLE, LINES, WEIGHT = range(6)
MOJIBAKE = {"\u00a1\u00a6": "..."}
BACK_SOURCE = "co_board.dxt"
BACK_REGIONS = {"back": (0.0176, 0.0801, 0.3691, 0.5488), "back_hover": (0.6426, 0.0098, 0.9941, 0.4785)}
FRAME_SETS = {"spin": "fx_taro_center_spin", "final": "fx_taro_final"}
CARD_REGION = (0.0039, 0.0039, 0.707, 0.9414)
FRAME_REGIONS = {"spin": (0.1133, 0.0, 0.8203, 0.9414), "final": CARD_REGION}


def crop(img, region):
    u0, v0, u1, v1 = region
    width, height = img.size
    return img.crop((round(u0 * width), round(v0 * height), round(u1 * width), round(v1 * height)))


def clean(text: str) -> str:
    for bad, good in MOJIBAKE.items():
        text = text.replace(bad, good)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tbl", default=str(DEFAULT_TBL))
    ap.add_argument("--ko", type=Path, default=bake_items.KO_ROOT)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    from kotools.tbl import read_tbl

    rows = read_tbl(args.tbl).rows
    if rows and len(rows[0]) != WEIGHT + 1:
        print(f"ERROR: expected {WEIGHT + 1} columns, table has {len(rows[0])}", file=sys.stderr)
        return 1

    hdr, src = args.ko / "UI" / "ui.hdr", args.ko / "UI" / "ui.src"
    bake_items._ensure_icon_deps()
    idx = _hdr_index(str(hdr))
    low = {k.lower(): k for k in idx}
    args.out.mkdir(parents=True, exist_ok=True)

    def image(key: str):
        real = low.get(key.lower())
        if real is None:
            return None
        offset, size = idx[real]
        with open(src, "rb") as f:
            f.seek(offset)
            return bake_items.decode_icon(f.read(size))

    categories = sorted({row[CATEGORY] for row in rows})
    cards = {}
    for category in categories:
        img = image(f"cardimg\\card{category:02d}.dxt")
        if img is None:
            print(f"[fortune] no art for card {category}", file=sys.stderr)
            continue
        name = f"card{category:02d}.png"
        crop(img, CARD_REGION).save(args.out / name)
        cards[str(category)] = name

    board = image(BACK_SOURCE)
    if board is None:
        print(f"[fortune] missing {BACK_SOURCE}", file=sys.stderr)
        return 1
    for stem, region in BACK_REGIONS.items():
        crop(board, region).save(args.out / f"{stem}.png")

    frames = {}
    for stem, prefix in FRAME_SETS.items():
        keys = sorted(k for k in low if re.fullmatch(rf"cardimg\\{prefix}_\d+\.dxt", k))
        names = []
        for i, key in enumerate(keys):
            img = image(key)
            if img is None:
                continue
            name = f"{stem}_{i:02d}.png"
            crop(img, FRAME_REGIONS[stem]).save(args.out / name)
            names.append(name)
        frames[stem] = names

    readings = [
        {
            "id": row[ID],
            "category": row[CATEGORY],
            "level": row[LEVEL],
            "title": clean(row[TITLE]),
            "lines": [clean(line) for line in row[LINES].split("\\")],
            "weight": row[WEIGHT],
        }
        for row in rows
    ]
    (args.out / "fortune.json").write_text(
        json.dumps({"cards": cards, "frames": frames, "readings": readings}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[fortune] {len(readings)} readings, {len(cards)} cards, "
          + ", ".join(f"{len(v)} {k} frames" for k, v in frames.items()) + f" -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

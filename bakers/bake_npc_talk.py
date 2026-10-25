"""bake_npc_talk.py -- NPC idle shouts and monster talk, and the speech balloon art.

Data\\NPC_Shout_us.tbl holds the lines an NPC says every interval and Data\\montalk_us.tbl a line per monster
HP bracket and on death. The client draws them in a balloon (ui\\co_monstersp.uif): a top, a middle stretched
by the line count and a bottom cut from ui\\monstersp.dxt. The cells below are that layout's: each piece is
its crop box in the sheet (left, top, right, bottom) and its place in the 236x89 balloon (x, y, width,
height); TEXT is the text box, with its colour and font height.

Output: <output>/npcs/npc_talk.json, <output>/ui/balloon/ (top.png, middle.png, bottom.png, layout.json)

Usage:
    python bake.py --only npc-talk
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_items  # noqa: E402
from fxb_parse import _hdr_index  # noqa: E402
from tbl_reader import read_tbl  # noqa: E402
from _paths import assets as _assets  # noqa: E402

DEFAULT_TALK = _assets() / "npcs" / "npc_talk.json"
DEFAULT_ART = _assets() / "ui" / "balloon"
SHEET = "monstersp.dxt"
SIZE = (236, 89)
PIECES = (
    ("top", ((17, 12, 203, 21), (23, 8, 186, 9))),
    ("middle", ((17, 28, 203, 68), (23, 17, 186, 17))),
    ("bottom", ((17, 75, 203, 94), (23, 57, 186, 19))),
)
TEXT = (39, 19, 154, 65)
TEXT_COLOR = "c0c0c0"
FONT_HEIGHT = 10
SHOUT_PROTO, SHOUT_CHANCE, SHOUT_INTERVAL, SHOUT_CHAT, SHOUT_TEXT = 1, 2, 3, 4, 5
TALK_SLOTS = 6
NO_LINE = "-"
CP949_SYMBOL = re.compile("\u00a1[\u00a1-\u00fe]")


def clean(text):
    return CP949_SYMBOL.sub(lambda m: m.group().encode("latin-1").decode("cp949"), text).strip()


def base_name(path: str) -> str:
    return path.lower().replace("\\", "/").rsplit("/", 1)[-1]


def bake_art(ko: Path, out: Path):
    bake_items._ensure_icon_deps()
    idx = _hdr_index(str(ko / "UI" / "ui.hdr"))
    real = next((k for k in idx if base_name(k) == SHEET), None)
    if real is None:
        raise SystemExit(f"[talk] {SHEET} not in the archive")
    offset, size = idx[real]
    with open(ko / "UI" / "ui.src", "rb") as src:
        src.seek(offset)
        sheet = bake_items.decode_icon(src.read(size)).convert("RGBA")
    out.mkdir(parents=True, exist_ok=True)
    layout = {"size": list(SIZE)}
    for piece, (box, rect) in PIECES:
        sheet.crop(box).save(out / f"{piece}.png")
        layout[piece] = list(rect)
    layout["text"] = list(TEXT)
    layout["color"] = TEXT_COLOR
    layout["fontHeight"] = FONT_HEIGHT
    (out / "layout.json").write_text(json.dumps(layout, indent=1), encoding="utf-8")


def bake_talk(data: Path, out: Path):
    shouts: dict[int, dict] = {}
    for row in read_tbl(data / "NPC_Shout_us.tbl").rows:
        entry = shouts.setdefault(row[SHOUT_PROTO], {"proto": row[SHOUT_PROTO], "interval": row[SHOUT_INTERVAL], "lines": []})
        entry["lines"].append({"chance": row[SHOUT_CHANCE], "chat": row[SHOUT_CHAT] != 0, "text": clean(row[SHOUT_TEXT])})
    monsters = []
    for row in read_tbl(data / "montalk_us.tbl").rows:
        lines = []
        for slot in range(TALK_SLOTS):
            text, chance = clean(row[1 + slot * 2]), row[2 + slot * 2]
            lines.append({"text": text, "chance": chance} if text and text != NO_LINE and chance > 0 else None)
        if any(lines):
            monsters.append({"proto": row[0], "lines": lines})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"shouts": list(shouts.values()), "monsters": monsters}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[talk] {len(shouts)} shouting NPCs, {len(monsters)} talking monsters -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", type=Path, default=bake_items.KO_ROOT)
    ap.add_argument("--talk", type=Path, default=DEFAULT_TALK)
    ap.add_argument("--art", type=Path, default=DEFAULT_ART)
    args = ap.parse_args()
    bake_art(args.ko, args.art)
    bake_talk(args.ko / "Data", args.talk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""bake_quest_text.py -- One-time: decrypt KO Quest_Menu / Quest_Talk .tbl files
into a plain JSON the client loads at runtime for NPC dialog text.

KO drives every NPC dialog with string IDs over the wire (GS_SELECT_MSG /
GS_NPC_SAY); the human-readable strings live client-side in Quest_Menu_*.tbl
(menu-button labels) and Quest_Talk_*.tbl (dialogue body lines). Both tables are
a simple [u32 id, string text] map (Quest_Talk has two trailing unused columns).

Output: <output>/quests/quest_text.json
    { "menu": { "<id>": "<label>", ... }, "talk": { "<id>": "<line>", ... } }

The runtime client (Domain/QuestText.cs) reads this JSON directly via FileAccess,
exactly like items.json -- it never touches Python, the cipher or the .tbl files.

Usage:
    python bake.py --only bake_quest_text --data "<install>\\Data"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

OUT = _assets() / "quests" / "quest_text.json"


def _load_map(data_dir: Path, stem: str) -> dict[str, str]:
    """Decode a [id, text, ...] table into an {str(id): text} dict."""
    src = data_dir / f"{stem}.tbl"
    if not src.exists():
        matches = [p for p in data_dir.glob("*.tbl") if p.stem.lower() == stem.lower()]
        if not matches:
            print(f"[quest_text] MISSING {stem}.tbl", file=sys.stderr)
            return {}
        src = matches[0]
    t = read_tbl(src)
    out: dict[str, str] = {}
    for row in t.rows:
        if len(row) < 2:
            continue
        out[str(int(row[0]))] = str(row[1])
    print(f"[quest_text] {stem}: {len(out)} rows")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Decrypt KO Quest_Menu/Quest_Talk .tbl -> quest_text.json")
    ap.add_argument("--data", help="KO Data dir containing Quest_*.tbl", default=str(_ko() / "Data"))
    ap.add_argument("--menu", default="Quest_Menu_us", help="menu table stem")
    ap.add_argument("--talk", default="Quest_Talk_us", help="talk table stem")
    ap.add_argument("--out", default=str(OUT), help="JSON output path")
    args = ap.parse_args()

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    payload = {
        "menu": _load_map(data_dir, args.menu),
        "talk": _load_map(data_dir, args.talk),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[quest_text] menu={len(payload['menu'])} talk={len(payload['talk'])} -> {out}")
    return 0 if payload["menu"] and payload["talk"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

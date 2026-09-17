"""bake_achievements.py -- decode ACHIEVE_main.tbl into the JSON the client needs to render
the achievement window.

Retail's `0x99` sub 3 row is only (id, state, progress, target) -- it carries no text at all, the
same trick the attendance board uses when it sends a slot number and lets the client look the
reward up. So every string in the window comes from here, keyed by achievement id.

ACHIEVE_main columns used:

    0  id
    1  category      1 war / 2 monster / 3 completion / 4 normal / 5 special.
                     Selects the condition table only (server-side concern, see
                     the server/tools/build_achievement_seed.py). It is NOT the window's tab.
    8  tab           0 normal / 1 quest / 2 war / 3 adventure / 4 challenge -- the six tabs the
                     window draws, the sixth being the total. Distinct from column 1: the two
                     partition the same 458 rows differently.
    2  title         the ACHIEVE_title row this achievement unlocks, 0 for none. The link is
                     symmetric and exact: title.col2 is the achievement id, and all 137 pairs
                     agree in both directions.
    3  points        achievement score, one of 10/20/30/40/50
    4  reward item   0 when the achievement pays only points
    5  reward count
    10 name
    11 objective     the "Defeat 10 Enemy User" line
    13 group         the window's section within a category

The title table (ACHIEVE_title.tbl) is a separate id space and belongs to the title sub-system,
which is not baked here.

    python bake.py --only bake_achievements

Writes <output>/achievements/achievements.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TBL = _ko() / "Data" / "ACHIEVE_main.tbl"
OUT = _assets() / "achievements" / "achievements.json"

ID, CATEGORY, TITLE, POINTS = 0, 1, 2, 3
TAB = 8
REWARD_ITEM, REWARD_COUNT = 4, 5
NAME, OBJECTIVE, GROUP = 10, 11, 13


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tbl", default=str(DEFAULT_TBL))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "tools"))
    from kotools.tbl import read_tbl

    rows = read_tbl(args.tbl).rows
    out = {}
    for row in rows:
        entry = {
            "cat": row[CATEGORY],
            "tab": row[TAB],
            "group": row[GROUP],
            "points": row[POINTS],
            "name": row[NAME],
            "objective": row[OBJECTIVE],
        }
        if row[REWARD_ITEM]:
            entry["item"] = row[REWARD_ITEM]
            entry["count"] = row[REWARD_COUNT]
        if row[TITLE]:
            entry["title"] = row[TITLE]
        out[str(row[ID])] = entry

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    categories = {}
    for entry in out.values():
        categories[entry["cat"]] = categories.get(entry["cat"], 0) + 1
    print(f"{len(out)} achievements -> {path}")
    print(f"  per category: {dict(sorted(categories.items()))}")
    print(f"  {sum(1 for e in out.values() if 'item' in e)} carry a reward item")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

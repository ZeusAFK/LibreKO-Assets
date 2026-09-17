"""bake_achievement_titles.py -- decode ACHIEVE_title.tbl into the JSON the client needs to show
which title an achievement unlocks and what it grants.

A title is unlocked by completing exactly one achievement. The link is symmetric and exact:
ACHIEVE_main col2 is the title id, ACHIEVE_title col2 is the achievement id, and all 137 pairs
agree in both directions.

The 25 bonus columns after `s_index` (columns 3..27) are named after the stat each one grants. Two
were confirmed in game first: `Expert` carries defence 40 and `Juraid Protector` defence 100.

    python bake.py --only bake_achievement_titles

Writes <output>/achievements/titles.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TBL = _ko() / "Data" / "ACHIEVE_title.tbl"
OUT = _assets() / "achievements" / "titles.json"

ID, NAME, ACHIEVEMENT = 0, 1, 2

BONUSES = [
    ("str", 3), ("hp", 4), ("dex", 5), ("int", 6), ("mp", 7),
    ("attack", 8), ("defence", 9), ("loyalty", 10), ("exp", 11),
    ("ac_shortsword", 12), ("ac_jamadar", 13), ("ac_sword", 14), ("ac_blow", 15),
    ("ac_axe", 16), ("ac_spear", 17), ("ac_arrow", 18),
    ("fire", 19), ("ice", 20), ("light", 21),
    ("r_fire", 22), ("r_ice", 23), ("r_light", 24),
    ("r_magic", 25), ("r_curse", 26), ("r_poison", 27),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tbl", default=str(DEFAULT_TBL))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "tools"))
    from kotools.tbl import read_tbl

    rows = read_tbl(args.tbl).rows
    expected = ACHIEVEMENT + 1 + len(BONUSES)
    if rows and len(rows[0]) != expected:
        print(f"ERROR: expected {expected} columns, table has {len(rows[0])}", file=sys.stderr)
        return 1

    out = {}
    for row in rows:
        entry = {"name": row[NAME], "achievement": row[ACHIEVEMENT]}
        bonus = {key: row[column] for key, column in BONUSES if row[column]}
        if bonus:
            entry["bonus"] = bonus
        out[str(row[ID])] = entry

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out)} titles -> {path}")
    print(f"  {sum(1 for e in out.values() if 'bonus' in e)} carry at least one bonus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

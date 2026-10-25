"""bake_manes_survival.py -- decode SurvivalSetting.tbl and MANES_MAGIC.tbl into the data the Manes' Survival
windows show.

SurvivalSetting columns: row, class, race, right hand, left hand, ten armour parts. MANES_MAGIC columns: id,
kind (1-4 skill level, 5 passive, 100 potion), code (the id every Manes' Survival packet names), magic,
Korean label, potion item, potion count, noah price, HP increase, attack increase %, attack decrease %.

Output: <output>/ui/manes_survival.json

Usage:
    python bake.py --only manes-survival
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROW, CLASS, RACE, RIGHT, LEFT, FIRST_ARMOUR = range(6)
ARMOUR_PARTS = 10
SETTING_COLUMNS = FIRST_ARMOUR + ARMOUR_PARTS
MAGIC_COLUMNS = 11
KIND, CODE, MAGIC, LABEL, ITEM, COUNT, PRICE, HP, ATTACK_UP, ATTACK_DOWN = range(1, MAGIC_COLUMNS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(_ko() / "Data"))
    ap.add_argument("--out", default=str(_assets() / "ui" / "manes_survival.json"))
    args = ap.parse_args()

    settings = read_tbl(Path(args.data) / "SurvivalSetting.tbl").rows
    magic = read_tbl(Path(args.data) / "MANES_MAGIC.tbl").rows
    for name, rows, width in (("SurvivalSetting", settings, SETTING_COLUMNS), ("MANES_MAGIC", magic, MAGIC_COLUMNS)):
        if rows and len(rows[0]) != width:
            print(f"ERROR: expected {width} {name} columns, table has {len(rows[0])}", file=sys.stderr)
            return 1

    looks = [{
        "row": r[ROW],
        "class": r[CLASS],
        "race": r[RACE],
        "right": r[RIGHT],
        "left": r[LEFT],
        "armour": list(r[FIRST_ARMOUR:FIRST_ARMOUR + ARMOUR_PARTS]),
    } for r in settings]

    picks = [{
        "code": r[CODE],
        "kind": r[KIND],
        "magic": r[MAGIC],
        "item": r[ITEM],
        "count": r[COUNT],
        "price": r[PRICE],
        "hp": r[HP],
        "attackUp": r[ATTACK_UP],
        "attackDown": r[ATTACK_DOWN],
    } for r in magic]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"looks": looks, "picks": picks}, indent=1), encoding="utf-8")
    print(f"[manes] {len(looks)} looks, {len(picks)} picks -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

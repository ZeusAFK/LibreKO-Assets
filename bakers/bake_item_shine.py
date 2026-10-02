"""Bake the high-grade shine level for every enchantable item.

Generic upgrade rows (rarity 5) and the Rebirth/Reverse ladders store the shine tier in
`Item_Ext_<cat>` **col12**:

    grade                        col12      shine level
    +6 and below                 <= 5000    none
    +7   == reverse +1..+4        6000       1   lightly blinking
    +8   == reverse +5..+10       7000       2   medium blinking
    +9   == reverse +11..+20      8000       3   blinks clearly
    +10  == reverse +21..+30      9000+      4   continuous

Named uniques (rarity 4) keep their real durability in that same column, so a +0 Chitin Shield
(durability 8000) would shine like a +9. Their upgrade step is the `(+N)` in the row name, and
the same +7..+10 tiers apply. A unique with no `(+N)` does not shine.

Output <output>/items/shine.json:
    { "cats": { "<cat>": { "<ext>": <level 1..4> } } }
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kotools.tbl.reader import read_tbl
from _paths import ko as _ko, assets as _assets  # noqa: E402

DATA = str(_ko() / "Data")
OUT = str(_assets() / "items" / "shine.json")

C_EXT, C_NAME, C_RARITY, C_GRADE_VALUE = 0, 1, 7, 12
MAX_LEVEL = 4
UNIQUE = 4
GRADE_THRESHOLDS = ((9000, 4), (8000, 3), (7000, 2), (6000, 1))


def shine_level(grade_value):
    if not isinstance(grade_value, int):
        return 0
    for floor, level in GRADE_THRESHOLDS:
        if grade_value >= floor:
            return level
    return 0


def plus_in_name(name):
    if not isinstance(name, str) or not name.endswith(")"):
        return 0
    open_at = name.rfind("(+")
    if open_at < 0:
        return 0
    try:
        return int(name[open_at + 2:-1])
    except ValueError:
        return 0


def shine_from_plus(plus):
    if plus >= 10:
        return 4
    if plus == 9:
        return 3
    if plus == 8:
        return 2
    if plus == 7:
        return 1
    return 0


def row_shine(row):
    rarity = row[C_RARITY] if len(row) > C_RARITY and isinstance(row[C_RARITY], int) else 0
    if rarity == UNIQUE:
        name = row[C_NAME] if len(row) > C_NAME else ""
        return shine_from_plus(plus_in_name(name))
    grade = row[C_GRADE_VALUE] if len(row) > C_GRADE_VALUE else 0
    return shine_level(grade)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--check", nargs="*", type=int, default=None,
                    help="explain these full item ids instead of writing the bake")
    args = ap.parse_args()

    org = read_tbl(os.path.join(args.data, "Item_Org_us.tbl"))
    base_cat = {r[0]: int(r[1]) for r in org.rows}

    cats, hist = {}, Counter()
    for path in sorted(glob.glob(os.path.join(args.data, "Item_Ext_*_us.tbl")),
                       key=lambda s: int(os.path.basename(s).split("_")[2])):
        cat = int(os.path.basename(path).split("_")[2])
        table = read_tbl(path)
        if len(table.column_types) <= C_GRADE_VALUE:
            continue
        rows = {}
        for r in table.rows:
            level = row_shine(r)
            if level:
                rows[str(r[C_EXT])] = level
                hist[level] += 1
        if rows:
            cats[str(cat)] = rows

    if args.check is not None:
        for full in args.check:
            base = full // 1000 * 1000
            cat = base_cat.get(base)
            ext = full - base
            level = cats.get(str(cat), {}).get(str(ext), 0)
            print(f"  {full}  base={base} cat={cat} ext={ext} -> shine level {level}")
        return

    out = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"cats": cats}, f, separators=(",", ":"), sort_keys=True)
    total = sum(len(v) for v in cats.values())
    print(f"[item_shine] {len(cats)} categories, {total} (cat,ext) shine entries "
          f"-> {out}")
    print("[item_shine] levels: " + "  ".join(f"L{k}={hist[k]}" for k in sorted(hist)))


if __name__ == "__main__":
    main()

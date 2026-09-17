"""Bake the high-grade shine level for every enchantable item.

`Item_Ext_<cat>` **col12** is a single unified "grade value" that already encodes the shine tier for
both the normal (+1..+10) and the Rebirth/Reverse (+1..+30) ladders, so no per-category special cases
are needed. Verified across 11 categories:

    grade                        col12      shine level
    +6 and below                 <= 5000    none
    +7   == reverse +1..+4        6000       1   lightly blinking
    +8   == reverse +5..+10       7000       2   medium blinking
    +9   == reverse +11..+20      8000       3   blinks clearly
    +10  == reverse +21..+30      9000+      4   continuous

The normal/reverse equivalence is the one documented on the KO wiki "Upgrading" page, and col12 holds
the *same* value on both sides of it (a +30 reverse and a +10 normal are both 9000), which is why one
threshold table covers every family -- including categories that repeat the 30-step ladder once per
element (cat 32 = Light 1..30, Frozen 31..60, named Glave(+N) 61..90).

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

C_EXT, C_GRADE_VALUE = 0, 12
MAX_LEVEL = 4
GRADE_THRESHOLDS = ((9000, 4), (8000, 3), (7000, 2), (6000, 1))


def shine_level(grade_value):
    if not isinstance(grade_value, int):
        return 0
    for floor, level in GRADE_THRESHOLDS:
        if grade_value >= floor:
            return level
    return 0


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
            level = shine_level(r[C_GRADE_VALUE])
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

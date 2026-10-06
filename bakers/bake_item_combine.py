"""bake_item_combine.py -- decode mixtable_us.tbl and MixLargeTbl_us.tbl into the data the item combination
windows show.

mixtable columns: id (the row the server's reply names), NPC, category, name, listed in the recipe book,
ten (item, count) materials, success effect, success motion, success balloon, failure effect, failure motion,
failure balloon, result item. MixLargeTbl columns: index, category, name, visibility (0 hidden, 1 shown,
2 Knight Royale only), icon.

    python bake.py --only bake_item_combine

Writes <output>/ui/item_combine.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = _ko() / "Data"
OUT = _assets() / "ui" / "item_combine.json"

MATERIALS = 10
ID, NPC, CATEGORY, NAME, LISTED, FIRST_MATERIAL = range(6)
OK_FX, OK_MOTION, OK_TEXT, FAIL_FX, FAIL_MOTION, FAIL_TEXT, RESULT = range(25, 32)
CAT_KEY, CAT_NAME, CAT_VISIBILITY = 1, 2, 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    from tbl_reader import read_tbl

    rows = read_tbl(Path(args.data) / "mixtable_us.tbl").rows
    if rows and len(rows[0]) != RESULT + 1:
        print(f"ERROR: expected {RESULT + 1} mixtable columns, table has {len(rows[0])}", file=sys.stderr)
        return 1
    groups = read_tbl(Path(args.data) / "MixLargeTbl_us.tbl").rows

    recipes = []
    for row in rows:
        materials = []
        for k in range(MATERIALS):
            item, count = row[FIRST_MATERIAL + 2 * k], row[FIRST_MATERIAL + 2 * k + 1]
            if item and count > 0:
                materials.append({"item": item, "count": count})
        recipes.append({
            "id": row[ID],
            "npc": row[NPC],
            "category": row[CATEGORY],
            "name": row[NAME],
            "listed": row[LISTED] == 1,
            "materials": materials,
            "result": row[RESULT],
            "successFx": row[OK_FX],
            "successMotion": row[OK_MOTION],
            "successText": row[OK_TEXT],
            "failureFx": row[FAIL_FX],
            "failureMotion": row[FAIL_MOTION],
            "failureText": row[FAIL_TEXT],
        })

    categories = [
        {"key": g[CAT_KEY], "name": g[CAT_NAME], "visibility": g[CAT_VISIBILITY]}
        for g in groups
    ]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"categories": categories, "recipes": recipes}, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    print(f"{len(recipes)} recipes, {len(categories)} categories -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

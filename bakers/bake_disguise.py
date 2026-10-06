"""bake_disguise.py -- decode DisguiseRing_us.tbl into the transformation list the client shows when a
Transformation Totem or Transformation Scroll is used.

Columns: id, name, minimum level, form skill, list item, access (1 everyone, 2 only without premium,
3 only with premium), note, class limit. The client keeps the rows whose list item is the used item.

    python bake.py --only bake_disguise

Writes <output>/skills/disguise.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TBL = _ko() / "Data" / "DisguiseRing_us.tbl"
OUT = _assets() / "skills" / "disguise.json"

ID, NAME, LEVEL, SKILL, ITEM, ACCESS, NOTE, CLASS_LIMIT = range(8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tbl", default=str(DEFAULT_TBL))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    from kotools.tbl import read_tbl

    rows = read_tbl(args.tbl).rows
    if rows and len(rows[0]) != CLASS_LIMIT + 1:
        print(f"ERROR: expected {CLASS_LIMIT + 1} columns, table has {len(rows[0])}", file=sys.stderr)
        return 1

    forms = [
        {
            "id": row[ID],
            "name": row[NAME],
            "level": row[LEVEL],
            "skill": row[SKILL],
            "item": row[ITEM],
            "access": row[ACCESS],
            "note": row[NOTE],
            "classLimit": row[CLASS_LIMIT],
        }
        for row in rows
    ]

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forms, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(forms)} transformation forms -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

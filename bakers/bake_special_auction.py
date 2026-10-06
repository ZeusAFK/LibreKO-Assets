"""bake_special_auction.py -- decode special_auction_us.tbl into the lot schedule Akara's Altar shows.

Columns: row id (14 * (group - 1) + day), then eight lots of item, count, start price, bid step and
secret. The server sends the group and the day; the client reads today's start prices and steps and the
rest of the group's days for the Schedule tab from this table.

    python bake.py --only bake_special_auction

Writes <output>/ui/special_auction.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TBL = _ko() / "Data" / "special_auction_us.tbl"
OUT = _assets() / "ui" / "special_auction.json"

LOTS = 8
LOT_COLUMNS = 5
ITEM, COUNT, START, STEP, SECRET = range(LOT_COLUMNS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tbl", default=str(DEFAULT_TBL))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    from tbl_reader import read_tbl

    rows = read_tbl(args.tbl).rows
    if rows and len(rows[0]) != 1 + LOTS * LOT_COLUMNS:
        print(f"ERROR: expected {1 + LOTS * LOT_COLUMNS} columns, table has {len(rows[0])}", file=sys.stderr)
        return 1

    lots = []
    for row in rows:
        for slot in range(LOTS):
            base = 1 + slot * LOT_COLUMNS
            if row[base + ITEM] == 0:
                continue
            lots.append({
                "row": row[0],
                "slot": slot,
                "item": row[base + ITEM],
                "count": row[base + COUNT],
                "start": row[base + START],
                "step": row[base + STEP],
                "secret": row[base + SECRET] == 1,
            })

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lots, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(lots)} auction lots -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

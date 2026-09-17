"""bake_tables.py -- One-time: decrypt KO .tbl files into a plain JSON intermediate.

This is step 1 of the bake. It reuses the verified tbl_reader.py decoder; the
decoded JSON is then turned into native Godot binary .res resources by the C#
tool (client/src/data/TblImport.cs). The runtime client never touches Python,
the cipher, or the .tbl files -- only the baked .res.

Usage:
    python bake.py --only bake_tables --data "<retail install>\\Data"
    python bake.py --only bake_tables --data DIR --tables NPC_us mob_us MON
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from tbl_reader import read_tbl  # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

# Tables the client currently needs. Add more here as features require them.
DEFAULT_TABLES = ["NPC_us", "mob_us", "MON"]

# JSON intermediate dir (build artifact; the .res files are the real output).
OUT_DIR = _assets() / "tables"


def _cell(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return 0.0
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="Decrypt KO .tbl -> JSON intermediate.")
    ap.add_argument("--data", help="KO Data dir containing *.tbl", default=str(_ko() / "Data"))
    ap.add_argument("--tables", nargs="+", default=DEFAULT_TABLES, metavar="NAME")
    ap.add_argument("--out", default=str(OUT_DIR), help="JSON output dir")
    args = ap.parse_args()

    data_dir = Path(args.data)
    out_dir = Path(args.out)
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for stem in args.tables:
        src = data_dir / f"{stem}.tbl"
        if not src.exists():
            # case-insensitive fallback
            matches = [p for p in data_dir.glob("*.tbl") if p.stem.lower() == stem.lower()]
            if not matches:
                print(f"[bake] MISSING {stem}.tbl", file=sys.stderr)
                continue
            src = matches[0]
        try:
            t = read_tbl(src)
        except Exception as e:
            print(f"[bake] {stem}: parse error: {e}", file=sys.stderr)
            continue
        payload = {
            "table_name": t.name,
            "column_types": [int(c) for c in t.column_types],
            "rows": [[_cell(v) for v in row] for row in t.rows],
        }
        dst = out_dir / f"{stem}.json"
        dst.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[bake] {stem}: rows={len(t.rows)} cols={len(t.column_types)} -> {dst}")
        ok += 1

    print(f"[bake] done ({ok}/{len(args.tables)}) -> {out_dir}")
    return 0 if ok == len(args.tables) else 1


if __name__ == "__main__":
    raise SystemExit(main())

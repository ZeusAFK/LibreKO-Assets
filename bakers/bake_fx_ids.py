"""bake_fx_ids.py -- retail ``Data/fx.tbl`` id -> baked effect name.

The server sends bare ``fx.tbl`` ids (the gathering reward effect, for one), while the
baked effects in ``<output>/fx/`` are keyed by file stem. This writes the lookup
that closes that gap, restricted to ids whose effect actually baked.

Run after ``bake_fx.py --all``; it only reads that output.

Usage:
    python bake.py --only bake_fx_ids
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from kotools.tbl import read_tbl                       # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_KO_DIR = _ko()
FX_DIR = _assets() / "fx"
DEFAULT_OUT = FX_DIR / "ids.json"

FX_NAME_COLUMN = 2


def normalized_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bake retail fx.tbl (effect id -> baked effect name) for the Godot client.")
    parser.add_argument("--ko-dir", default=str(DEFAULT_KO_DIR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    rows = read_tbl(str(Path(args.ko_dir) / "Data" / "fx.tbl")).rows
    baked = {p.stem for p in FX_DIR.glob("*.json") if p.name not in ("index.json", "ids.json")}
    by_lower = {name.lower(): name for name in baked}

    ids: dict[str, str] = {}
    unbaked = 0
    for row in rows:
        if len(row) <= FX_NAME_COLUMN or not isinstance(row[FX_NAME_COLUMN], str):
            continue
        stem = normalized_stem(row[FX_NAME_COLUMN])
        if not stem:
            continue
        match = by_lower.get(stem.lower())
        if match is None:
            unbaked += 1
            continue
        ids[str(int(row[0]))] = match

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ids, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"[fx-ids] {len(ids)} of {len(rows)} fx.tbl rows -> {out} ({unbaked} not baked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

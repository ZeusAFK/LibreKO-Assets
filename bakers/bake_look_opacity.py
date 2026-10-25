"""bake_look_opacity.py -- the fixed opacity of NPC looks.

The client multiplies a look's alpha by NPC_Looks columns 36 and 37 (percent, 100 = opaque) every frame.
Only looks below 100% are written.

Output: <output>/npcs/look_opacity.json

Usage:
    python bake.py --only look-opacity
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_mobs as bm  # noqa: E402
from _paths import assets as _assets  # noqa: E402

OPACITY_A, OPACITY_B = 36, 37
FULL = 100
OUT = _assets() / "npcs" / "look_opacity.json"


def main() -> int:
    looks = {}
    for row in bm.read_tbl(bm.NPC_LOOKS).rows:
        if not row or len(row) <= OPACITY_B or not isinstance(row[0], int):
            continue
        a, b = int(row[OPACITY_A] or 0), int(row[OPACITY_B] or 0)
        if a == FULL and b == FULL:
            continue
        looks[str(row[0])] = round(a / FULL * b / FULL, 4)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dict(sorted(looks.items(), key=lambda kv: int(kv[0]))), indent=1) + "\n", encoding="utf-8")
    print(f"[look_opacity] {len(looks)} looks -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

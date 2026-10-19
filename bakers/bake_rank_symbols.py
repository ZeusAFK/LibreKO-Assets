r"""bake_rank_symbols.py -- bake the national-rank marks shown beside a ranked player's name.

The client loads eighteen images from `res://assets/ui/rank_symbol/` (Domain/RankSymbol.cs):
`knights_<tier>.png` and `personal_<tier>.png` for name plates and the character window, and
`tier_<tier>.png` for the chat log. They are not in `UI\ui.hdr`: retail files them as loose
textures in `symbol_us\` beside the clan symbols, under clan id 0, and each file's header still
carries the artist's original path, which names the tier.

    s_0_20_0 .. s_0_25_0   16x16, gold border   -> knights_0..5   (all-time points ranking)
    s_0_30_0 .. s_0_35_0   16x16, black border  -> personal_0..5  (monthly points ranking)
    s_0_40_0 .. s_0_45_0   64x64, art top-left  -> tier_0..5      (the chat-window ladder bar)

Tier n is RankSymbol.TierOf: 0 rank 1 (Gold Knight), 1 ranks 2-4 (Silver), 2 ranks 5-10
(Mirage), 3 ranks 11-40 (Shadow), 4 ranks 41-100 (Mist), 5 ranks 101-200 (Training).

The ladder bars sit in the top-left of a 64x64 texture and get shorter with the tier, centred on
the widest one, so the whole group is cropped to one shared box: every bar keeps the same size,
as the chat log draws them at a fixed size. Left out on purpose: s_0_50..55 (byte-identical to
30..35) and ani_0_40..42 (the animated top-three bars; the client loads static PNGs only).

Output: <output>/ui/rank_symbol/{knights,personal,tier}_<0..5>.png

Usage:
    python bake.py --only rank-symbols
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

DEFAULT_SRC = _ko() / "symbol_us"
OUT_DIR = _assets() / "ui" / "rank_symbol"
GROUPS = (("knights", 20), ("personal", 30), ("tier", 40))
TIERS = 6


def bake(src_dir: Path, out_dir: Path) -> tuple[int, list[str]]:
    import numpy as np
    from PIL import Image

    from ko_texture import decode_ntf

    out_dir.mkdir(parents=True, exist_ok=True)
    baked, missing = 0, []
    for name, first in GROUPS:
        images = {}
        for tier in range(TIERS):
            src = src_dir / f"s_0_{first + tier}_0.dxt"
            decoded = decode_ntf(src.read_bytes()) if src.is_file() else None
            if decoded is None:
                missing.append(src.name)
                continue
            width, height, rgba = decoded
            images[tier] = Image.frombytes("RGBA", (width, height), np.asarray(rgba).tobytes())

        boxes = [box for image in images.values() if (box := image.getchannel("A").getbbox())]
        if boxes:
            shared = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                      max(b[2] for b in boxes), max(b[3] for b in boxes))
            images = {tier: image.crop(shared) for tier, image in images.items()}
        for tier, image in images.items():
            image.save(out_dir / f"{name}_{tier}.png", "PNG", optimize=True)
            baked += 1
    return baked, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake the KO national-rank symbols.")
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    baked, missing = bake(Path(args.src), Path(args.out))
    print(f"[rank] {baked}/{len(GROUPS) * TIERS} symbols -> {args.out}")
    for name in missing:
        print(f"[rank] missing: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

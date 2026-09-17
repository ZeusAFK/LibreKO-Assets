r"""bake_moon.py -- Bake the moon disc texture from retail's sky data.

The `.n3sky` header's tenth name slot (the "moon name" in ATMOSPHERE_REFERENCE.md) is
`misc\sky\phases.tga` for every zone: a 384x256 sheet holding a 6x4 grid of 64x64
photographic moon phases, alpha-keyed, waxing left-to-right / top-to-bottom. Row 2 col 0
is the fullest phase.

The maria are in the ALPHA channel, not in RGB: across the disc face RGB is a flat cream
(std 7 of 255) while alpha runs 101..251 (std 37). Retail draws this sheet blended, so alpha
carries the moon's tone and RGB only tints it. Baking the file as-is gives a blank cream
ball. So: luminance comes from alpha, the cream stays as a tint, and the OPACITY is
re-authored as a clean circle -- alpha's own falloff at the rim is antialiasing, and reusing
it as tone paints a black ring round the limb.

Output: <output>/sky/moon.png -- 128x128 RGBA, disc radius `DISC_FILL` of half-width.
`Sky.MoonAngularRadius` is the PADDED half-angle, so the visible moon is DISC_FILL of it.

Usage:
    python bake.py --only bake_moon

Godot only writes .import sidecars from the editor, so after a re-bake run:
    Godot_v4.7.2-stable_mono_win64_console.exe --headless --path client --import
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from _paths import ko as _ko, assets as _assets  # noqa: E402

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

DEFAULT_SHEET = _ko() / "Misc" / "Sky" / "phases.tga"
OUT = _assets() / "sky" / "moon.png"

TILE = 64
FULL_TILE = (2, 0)
OUT_SIZE = 128
DISC_FILL = 0.92
FEATHER_PX = 2.0
ALPHA_FLOOR = 24
PAD = 8
FACE_INSET = 0.93
FACE_GROW = 8
LIMB_START = 0.55
LIMB_DARKENING = 0.22


def bake(sheet: Path, out: Path) -> tuple[int, float]:
    import numpy as np
    from PIL import Image

    sheet_px = np.array(Image.open(sheet).convert("RGBA"))
    row, col = FULL_TILE
    tile = sheet_px[row * TILE:(row + 1) * TILE, col * TILE:(col + 1) * TILE]

    ys, xs = np.nonzero(tile[..., 3] > ALPHA_FLOOR)
    if len(xs) == 0:
        raise SystemExit(f"{sheet}: tile {FULL_TILE} is empty")
    cx, cy = (xs.min() + xs.max() + 1) / 2.0, (ys.min() + ys.max() + 1) / 2.0
    radius = max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) / 2.0

    # The disc all but fills its 64px cell, so the padded crop box runs off the tile.
    tile = np.pad(tile, ((PAD, PAD), (PAD, PAD), (0, 0)))
    cx += PAD
    cy += PAD
    half = radius / DISC_FILL
    crop = Image.fromarray(tile).resize(
        (OUT_SIZE, OUT_SIZE), Image.LANCZOS,
        box=(cx - half, cy - half, cx + half, cy + half))
    px = np.array(crop).astype(np.float32)

    gy, gx = np.mgrid[0:OUT_SIZE, 0:OUT_SIZE]
    r = np.hypot(gx + 0.5 - OUT_SIZE / 2.0, gy + 0.5 - OUT_SIZE / 2.0) / (OUT_SIZE / 2.0)
    face = r < DISC_FILL * FACE_INSET

    lum = px[..., 3] / max(np.percentile(px[..., 3][face], 99.5), 1.0)
    tint = px[..., :3] / max(np.percentile(px[..., :3][face], 99.5), 1.0)
    rgb = np.clip(tint * lum[..., None], 0.0, 1.0)

    inside = face.copy()
    for _ in range(FACE_GROW):
        grown = inside.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            take = np.roll(np.roll(inside, dy, 0), dx, 1) & ~grown
            if not take.any():
                continue
            rgb[take] = np.roll(np.roll(rgb, dy, 0), dx, 1)[take]
            grown |= take
        inside = grown

    limb = 1.0 - LIMB_DARKENING * np.clip((r - LIMB_START) / (DISC_FILL - LIMB_START), 0.0, 1.0) ** 2
    rgb *= limb[..., None]

    edge = FEATHER_PX / (OUT_SIZE / 2.0)
    alpha = np.clip((DISC_FILL - r) / edge, 0.0, 1.0)
    alpha = alpha * alpha * (3.0 - 2.0 * alpha)

    data = np.dstack([rgb, alpha[..., None]])
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((data * 255.0 + 0.5).astype("uint8"), "RGBA").save(out, "PNG", optimize=True)
    return OUT_SIZE, radius


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    if not args.sheet.is_file():
        print(f"missing {args.sheet}", file=sys.stderr)
        return 1
    size, radius = bake(args.sheet, args.out)
    print(f"[moon] {args.out}  {size}x{size}  source disc radius {radius:.1f}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

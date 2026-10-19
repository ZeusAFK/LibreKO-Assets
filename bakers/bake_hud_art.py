r"""bake_hud_art.py -- bake the War Board buttons and the anger gauge sheet out of UI\ui.hdr/.src.

War Board. Features/Hud/PartyWarBoardFeature.cs loads nine 16x16 buttons from
`res://assets/ui/war_board/`. They are cells of `common_button_0.dxt` (1024x1024), the button atlas
re_damagemeter.uif and re_damagemeter_party.uif draw from, laid out on a 17-pixel grid:

             x=901          918            935            952        969         986
    y=6      close          close_hover    close_pressed  deal_on    deal_hover  deal_off
    y=23     heal_on        heal_hover     heal_off

The client builds the close button from close/close_hover/close_pressed and each tab from
<tab>_off (normal), <tab>_hover and <tab>_on (pressed). Left out on purpose: the meter cells the
client no longer loads -- meter_board 1003,6 and meter_board_hover 1003,23, meter_toggle,
meter_toggle_hover and meter_toggle_off at 952,23 / 969,23 / 986,23.

Anger gauge. Features/Hud/AngerGaugeFeature.cs draws its frames out of one sheet, `anger.dxt`
(512x512, the only texture co_anger.uif names), written whole: Domain/AngerGauge.cs holds the
39x59 frame rects, the gauge steps along y=8 and the full-gauge animation along y=72.

Both textures are NTF v7 A8R8G8B8 and go through bake_items.decode_icon (the per-row-reset RC4
decrypt). The skill-ready flash the hotbar loads (ui/hotbar_ready/ready_00..14) is not baked: its
retail source has not been found in ui.hdr.

Output: <output>/ui/war_board/<button>.png and <output>/ui/anger/anger.png

Usage:
    python bake.py --only hud-art
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bake_items                                        # noqa: E402  (decode_icon + KO_ROOT)
from fxb_parse import _hdr_index                          # noqa: E402
from _paths import assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "ui"
BUTTON_ATLAS = "common_button_0.dxt"
BUTTON = 16
# (output stem, x, y of the cell's top-left corner in common_button_0.dxt)
WAR_BOARD = (
    ("close", 901, 6), ("close_hover", 918, 6), ("close_pressed", 935, 6),
    ("deal_on", 952, 6), ("deal_hover", 969, 6), ("deal_off", 986, 6),
    ("heal_on", 901, 23), ("heal_hover", 918, 23), ("heal_off", 935, 23),
)
ANGER_SHEET = "anger.dxt"


def bake(hdr: Path, src: Path, out_dir: Path) -> tuple[int, list[str]]:
    idx = _hdr_index(str(hdr))
    low = {k.lower(): k for k in idx}

    def texture(f, key: str):
        real = low.get(key.lower())
        if real is None:
            return None
        offset, size = idx[real]
        f.seek(offset)
        return bake_items.decode_icon(f.read(size))

    baked, failed = 0, []
    with open(src, "rb") as f:
        atlas = texture(f, BUTTON_ATLAS)
        if atlas is None:
            failed.append(BUTTON_ATLAS)
        else:
            board_dir = out_dir / "war_board"
            board_dir.mkdir(parents=True, exist_ok=True)
            for stem, x, y in WAR_BOARD:
                cell = atlas.crop((x, y, x + BUTTON, y + BUTTON))
                cell.save(board_dir / f"{stem}.png", "PNG", optimize=True)
                baked += 1

        sheet = texture(f, ANGER_SHEET)
        if sheet is None:
            failed.append(ANGER_SHEET)
        else:
            anger_dir = out_dir / "anger"
            anger_dir.mkdir(parents=True, exist_ok=True)
            sheet.save(anger_dir / "anger.png", "PNG", optimize=True)
            baked += 1
    return baked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake the KO War Board buttons and anger gauge sheet.")
    parser.add_argument("--ko", type=Path, default=bake_items.KO_ROOT, help="retail KnightOnlineEn root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the ui output dir")
    args = parser.parse_args()

    hdr, src = args.ko / "UI" / "ui.hdr", args.ko / "UI" / "ui.src"
    if not hdr.exists() or not src.exists():
        print(f"[hud-art] missing archive: {hdr} / {src}", file=sys.stderr)
        return 2

    baked, failed = bake(hdr, src, args.out)
    print(f"[hud-art] {baked}/{len(WAR_BOARD) + 1} images -> {args.out}")
    for key in failed:
        print(f"[hud-art] missing or undecodable: ui\\{key}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

r"""bake_hud_art.py -- bake the War Board buttons and the anger gauge sheet out of UI\ui.hdr/.src,
and the skill-ready flash out of Misc\RecastObject.

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
decrypt).

Ready flash. Domain/ReadyFlash.cs plays fifteen frames over a hotbar slot whose cooldown ends,
`ready_00..14`. They are loose textures, not in ui.hdr: Misc\RecastObject\64shot0000..0002
(64x64) are ready_00..02 and skill_up_fx_0000..0011 (32x32) are ready_03..14. skill_up_fx_0012
is not part of the sequence.

Familiar auto-cast. A familiar bar slot with automatic cast on loops Misc\RecastObject\final_01_0000..0019,
`auto_00..19`.

Output: <output>/ui/war_board/<button>.png, <output>/ui/anger/anger.png,
<output>/ui/hotbar_ready/ready_<nn>.png and <output>/ui/familiar_auto/auto_<nn>.png

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
READY_DIR = Path("Misc") / "RecastObject"
READY_SEQUENCES = (("64shot", 3), ("skill_up_fx_", 12))
FAMILIAR_AUTO_FRAMES = tuple(f"final_01_{index:04d}.dxt" for index in range(20))
READY_FRAMES = tuple(f"{prefix}{index:04d}.dxt" for prefix, count in READY_SEQUENCES for index in range(count))


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


def bake_sequence(recast_dir: Path, frames_dir: Path, stem: str, names: tuple[str, ...]) -> tuple[int, list[str]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    baked, failed = 0, []
    for index, name in enumerate(names):
        path = recast_dir / name
        frame = bake_items.decode_icon(path.read_bytes()) if path.is_file() else None
        if frame is None:
            failed.append(name)
            continue
        frame.save(frames_dir / f"{stem}_{index:02d}.png", "PNG", optimize=True)
        baked += 1
    return baked, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake the KO War Board buttons, anger gauge sheet and skill-ready flash.")
    parser.add_argument("--ko", type=Path, default=bake_items.KO_ROOT, help="retail KnightOnlineEn root")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the ui output dir")
    args = parser.parse_args()

    hdr, src = args.ko / "UI" / "ui.hdr", args.ko / "UI" / "ui.src"
    if not hdr.exists() or not src.exists():
        print(f"[hud-art] missing archive: {hdr} / {src}", file=sys.stderr)
        return 2

    baked, failed = bake(hdr, src, args.out)
    ready_baked, ready_failed = bake_sequence(args.ko / READY_DIR, args.out / "hotbar_ready", "ready", READY_FRAMES)
    auto_baked, auto_failed = bake_sequence(args.ko / READY_DIR, args.out / "familiar_auto", "auto", FAMILIAR_AUTO_FRAMES)
    ready_baked += auto_baked
    ready_failed += auto_failed
    total = len(WAR_BOARD) + 1 + len(READY_FRAMES) + len(FAMILIAR_AUTO_FRAMES)
    print(f"[hud-art] {baked + ready_baked}/{total} images -> {args.out}")
    for key in failed:
        print(f"[hud-art] missing or undecodable: ui\\{key}", file=sys.stderr)
    for name in ready_failed:
        print(f"[hud-art] missing or undecodable: {READY_DIR / name}", file=sys.stderr)
    return 1 if failed or ready_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

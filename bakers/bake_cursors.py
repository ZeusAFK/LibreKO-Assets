r"""bake_cursors.py -- bake the retail KO mouse cursors out of UI\ui.hdr/.src into PNGs.

Retail does NOT use Win32 cursors for the in-game pointer: the cursor manager loads `ui\cursor.uif`, binds
SEVEN image controls named `Image_Cursor00..06`, asks the OS every frame to keep
the OS arrow hidden, and each frame draws image[type] with its TOP-LEFT at the raw mouse position
(so the art's hotspot is baked in at 0,0).

The .uif gives control -> texture, and the cursor renderer indexes the image array
DIRECTLY by cursor type, so type == control index:

    type 0  Image_Cursor00  ui\cursorattack.dxt      sword          (attack)
    type 1  Image_Cursor01  ui\cursorel01.dxt        dark blue      (El Morad, idle)
    type 2  Image_Cursor02  ui\cursorel02.dxt        bright cyan    (El Morad, button held)
    type 3  Image_Cursor03  ui\cursorka01.dxt        dark red       (Karus, idle -- also the
                                                                    pre-nation default)
    type 4  Image_Cursor04  ui\cursorka02.dxt        bright orange  (Karus, button held)
    type 5  Image_Cursor05  ui\cursorrepair01.dxt    hammer         (repair mode)
    type 6  Image_Cursor06  ui\cursorrepair02.dxt    hammer, struck (repair, second mode)

Types 7..10 exist in the HCURSOR->type shim but Render bails on `type > 6`, i.e. they
draw NOTHING. Nation comes from the player's nation field (2 = El Morad, else Karus) and the idle/held
split from the input mouse-button flags -- see the frame loop and GameCursor.cs.

All seven are 32x32. Six are NTF v7 A4R4G4B4 (the per-row-reset RC4 decrypt -- see bake_items.py);
cursorel01 is v3 A8R8G8B8 with mips. Both go through bake_items.decode_icon.

Usage:
    python bake.py --only bake_cursors
    python bake.py --only bake_cursors --ko str(_ko()) --out <output>/ui/cursors
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "docs"))      # fxb_parse.py

import bake_items                                        # noqa: E402  (decode_icon + KO_ROOT)
from fxb_parse import _hdr_index                          # noqa: E402
from _paths import ko as _ko, assets as _assets  # noqa: E402

DEFAULT_OUT = _assets() / "ui" / "cursors"

# (retail cursor type, archive key, output stem, what it is)
CURSORS = [
    (0, "cursorattack.dxt", "attack", "sword -- attack"),
    (1, "cursorel01.dxt", "el_idle", "El Morad arrow, idle"),
    (2, "cursorel02.dxt", "el_held", "El Morad arrow, button held"),
    (3, "cursorka01.dxt", "ka_idle", "Karus arrow, idle (default before nation is known)"),
    (4, "cursorka02.dxt", "ka_held", "Karus arrow, button held"),
    (5, "cursorrepair01.dxt", "repair", "hammer -- repair mode"),
    (6, "cursorrepair02.dxt", "repair_alt", "hammer struck -- repair, second mode"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", type=Path, default=bake_items.KO_ROOT, help="retail KnightOnlineEn root")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output dir for the PNGs")
    args = ap.parse_args()

    hdr, src = args.ko / "UI" / "ui.hdr", args.ko / "UI" / "ui.src"
    if not hdr.exists() or not src.exists():
        print(f"[cursors] missing archive: {hdr} / {src}", file=sys.stderr)
        return 2

    bake_items._ensure_icon_deps()
    idx = _hdr_index(str(hdr))
    low = {k.lower(): k for k in idx}
    args.out.mkdir(parents=True, exist_ok=True)

    manifest, failed = [], []
    with open(src, "rb") as f:
        for ctype, key, stem, what in CURSORS:
            real = low.get(key.lower())
            if real is None:
                print(f"[cursors] NOT IN ARCHIVE: {key}", file=sys.stderr)
                failed.append(key)
                continue
            offset, size = idx[real]
            f.seek(offset)
            img = bake_items.decode_icon(f.read(size))
            if img is None:
                print(f"[cursors] decode failed: {key}", file=sys.stderr)
                failed.append(key)
                continue
            path = args.out / f"{stem}.png"
            img.save(path)
            manifest.append({
                "type": ctype, "control": f"Image_Cursor{ctype:02d}", "source": f"ui\\{key}",
                "png": path.name, "size": list(img.size), "hotspot": [0, 0], "what": what,
            })
            print(f"[cursors] type{ctype} {key:22} {img.size} -> {path.name}")

    (args.out / "cursors.json").write_text(
        json.dumps({
            "note": "Retail KO cursors from ui.hdr. Hotspot is the image TOP-LEFT (0,0): "
                    "the original client places the image origin at the raw mouse position.",
            "uif": "ui\\cursor.uif",
            "cursors": manifest,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[cursors] {len(manifest)}/{len(CURSORS)} baked -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

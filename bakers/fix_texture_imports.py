r"""fix_texture_imports.py -- Godot import sidecars for the textures 3D materials sample.

Godot's default sidecar for a PNG is lossless (`compress/mode=0`): the texture is decoded from
WebP at load and held in VRAM as raw RGBA8. The editor's 3D detection that would switch it to
VRAM compression never runs in a headless `--import`, so every model texture shipped that way.
This step gives the folders below `compress/mode=2` (BC1/BC3 on desktop, ETC2/ASTC on Android,
the family retail's own .dxt files used) and mipmaps. A PNG that has no sidecar yet gets a
minimal one; Godot fills in the rest on the next import. Icons, UI and backgrounds are 2D and
stay lossless.

Usage:
    python bake.py --only texture-imports

Then, as after any bake:
    Godot_v4.7.2-stable_mono_win64_console.exe --headless --path client --import
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import assets as _assets  # noqa: E402

FOLDERS = (
    "objects", "npcs", "characters",
    "items/armor", "items/weapon", "items/clanaddon",
    "wings", "capes",
)

MINIMAL_SIDECAR = (
    '[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\n\n'
    '[params]\n\ncompress/mode=2\nmipmaps/generate=true\n'
)


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    out = _assets()
    written = rewritten = 0
    for folder in FOLDERS:
        root = out / folder
        if not root.is_dir():
            continue
        for png in root.rglob("*.png"):
            sidecar = png.with_name(png.name + ".import")
            if not sidecar.exists():
                _write(sidecar, MINIMAL_SIDECAR)
                written += 1
                continue
            text = sidecar.read_text(encoding="utf-8")
            if 'importer="texture"' not in text:
                continue
            new = re.sub(r"^compress/mode=0$", "compress/mode=2", text, flags=re.M)
            new = re.sub(r"^mipmaps/generate=false$", "mipmaps/generate=true", new, flags=re.M)
            if new != text:
                _write(sidecar, new)
                rewritten += 1
    print(f"[texture-imports] new sidecars {written}, rewritten {rewritten}"
          + (" -- run the Godot --import" if written or rewritten else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

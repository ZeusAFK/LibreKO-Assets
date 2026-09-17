from __future__ import annotations

import os
from pathlib import Path

KO_ENV = "LIBREKO_KO_DIR"
OUT_ENV = "LIBREKO_OUT_DIR"
SERVER_ENV = "LIBREKO_SERVER_DATA"

MARKERS = ("Data", "Zones", "DTex")


class LayoutError(RuntimeError):
    pass


def ko_dir() -> Path:
    value = os.environ.get(KO_ENV)
    if not value:
        raise LayoutError(f"{KO_ENV} is unset; run bake.py rather than a baker directly")
    return Path(value)


def out_dir() -> Path:
    value = os.environ.get(OUT_ENV)
    if not value:
        raise LayoutError(f"{OUT_ENV} is unset; run bake.py rather than a baker directly")
    return Path(value)


def data_dir() -> Path:
    return ko_dir() / "Data"


def zones_dir() -> Path:
    return ko_dir() / "Zones"


def dtex_dir() -> Path:
    return ko_dir() / "DTex"


def default_server_data(out: Path) -> Path | None:
    """The LibreKO checkout keeps Client/assets beside Server/LibreKO.Game/Seed/Data."""
    candidate = out.parent.parent / "Server" / "LibreKO.Game" / "Seed" / "Data"
    return candidate if (candidate / "Magic.json").is_file() else None


def missing_markers(root: Path) -> list[str]:
    return [name for name in MARKERS if not (root / name).is_dir()]


def bind(ko: Path, out: Path) -> None:
    os.environ[KO_ENV] = str(ko)
    os.environ[OUT_ENV] = str(out)

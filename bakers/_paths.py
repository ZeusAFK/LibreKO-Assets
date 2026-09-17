"""Path resolution for the vendored bakers.

Every baker reads its retail-install root and its output root from the environment, so the
same scripts run on any machine without edits. bake.py sets both before dispatching.
"""
from __future__ import annotations

import os
from pathlib import Path

KO_ENV = "LIBREKO_KO_DIR"
OUT_ENV = "LIBREKO_OUT_DIR"
SERVER_ENV = "LIBREKO_SERVER_DATA"


def _require(var: str) -> Path:
    value = os.environ.get(var)
    if not value:
        raise SystemExit(
            f"{var} is not set.\n"
            "Run the pipeline through bake.py, which resolves --ko and --out for every step."
        )
    return Path(value)


def ko() -> Path:
    return _require(KO_ENV)


def assets() -> Path:
    return _require(OUT_ENV)


def server_data() -> Path | None:
    value = os.environ.get(SERVER_ENV)
    return Path(value) if value else None

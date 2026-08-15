"""Resolve the application's portable runtime-data folder in one place."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_FOLDER = "CivitaiArtistDiscovery"
LEGACY_APP_FOLDER = "CivitaiArtistHistory"
DATA_DIR_ENV = "CIVITAI_HISTORY_DATA_DIR"


def application_root() -> Path:
    """Return the folder containing the executable, or the source checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")


def data_root() -> Path:
    """Use portable ``data/`` storage, preserving an existing install on upgrade.

    The environment override is retained for development and isolated tests. Normal
    launches always use a folder beside the executable (or source checkout). An older
    LocalAppData installation is moved there once. If permissions block that move, the
    old folder remains active so archives and credentials never appear to be lost.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    portable = application_root() / "data"
    if portable.exists():
        return portable
    current, legacy = _base() / APP_FOLDER, _base() / LEGACY_APP_FOLDER
    existing = current if current.exists() else legacy if legacy.exists() else None
    if existing is None:
        return portable
    try:
        os.rename(existing, portable)
        return portable
    except OSError:
        return existing

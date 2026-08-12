"""Where this application keeps its per-user data.

One resolver, imported by everything that needs the path. An earlier build derived it
independently in two places and the copies drifted, which left OAuth tokens outside the
documented folder; keeping a single definition is what prevents that recurring.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_FOLDER = "CivitaiArtistDiscovery"
# The folder used before the application was renamed. Kept only so it can be moved.
LEGACY_APP_FOLDER = "CivitaiArtistHistory"
DATA_DIR_ENV = "CIVITAI_HISTORY_DATA_DIR"


def _base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")


def data_root() -> Path:
    """The data directory, moving an older installation's folder across on first use.

    An explicit override always wins. Otherwise the renamed folder is preferred, and a
    folder left by the previous name is moved rather than abandoned — it holds the day
    archives, the taste database, and the OAuth token. If the move cannot be made, the old
    folder keeps being used, because losing sight of a user's archives is far worse than an
    inconsistent folder name.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    current, legacy = _base() / APP_FOLDER, _base() / LEGACY_APP_FOLDER
    if current.exists() or not legacy.exists():
        return current
    try:
        os.rename(legacy, current)
        return current
    except OSError:
        return legacy

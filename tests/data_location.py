"""The data folder has one definition, and a rename never orphans a user's archives."""

import importlib
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def reload_paths(local_app_data, override=None):
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    if override is None:
        os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)
    else:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = str(override)
    import discovery.paths as paths
    return importlib.reload(paths)


original_local = os.environ.get("LOCALAPPDATA")
original_override = os.environ.get("CIVITAI_HISTORY_DATA_DIR")
try:
    # A fresh machine simply uses the current folder name.
    with tempfile.TemporaryDirectory(prefix="loc-fresh-") as base:
        paths = reload_paths(base)
        root = paths.data_root()
        assert root == Path(base) / "CivitaiArtistDiscovery", root
        assert not root.exists(), "resolving the path should not create it"

    # A folder left by the previous name is moved, with its contents.
    with tempfile.TemporaryDirectory(prefix="loc-legacy-") as base:
        paths = reload_paths(base)
        legacy = Path(base) / "CivitaiArtistHistory"
        (legacy / "history").mkdir(parents=True)
        (legacy / "history" / "history.sqlite3").write_bytes(b"archive")
        (legacy / "oauth_tokens.dpapi").write_bytes(b"token")
        root = paths.data_root()
        assert root == Path(base) / "CivitaiArtistDiscovery", root
        assert not legacy.exists(), "the old folder was left behind"
        assert (root / "history" / "history.sqlite3").read_bytes() == b"archive"
        assert (root / "oauth_tokens.dpapi").read_bytes() == b"token"

    # If both exist, the current folder wins and the old one is left untouched rather
    # than merged, because merging could silently overwrite a live archive.
    with tempfile.TemporaryDirectory(prefix="loc-both-") as base:
        paths = reload_paths(base)
        legacy, current = Path(base) / "CivitaiArtistHistory", Path(base) / "CivitaiArtistDiscovery"
        legacy.mkdir(); current.mkdir()
        (legacy / "marker").write_bytes(b"old")
        assert paths.data_root() == current
        assert (legacy / "marker").exists()

    # An explicit override always wins and never triggers a move.
    with tempfile.TemporaryDirectory(prefix="loc-override-") as base:
        chosen = Path(base) / "elsewhere"
        paths = reload_paths(base, override=chosen)
        legacy = Path(base) / "CivitaiArtistHistory"
        legacy.mkdir()
        assert paths.data_root() == chosen
        assert legacy.exists(), "an override must not move anything"

    # Every module that needs the folder asks the same resolver, which is what stops the
    # copies drifting the way the OAuth token path once did.
    with tempfile.TemporaryDirectory(prefix="loc-shared-") as base:
        reload_paths(base)
        import discovery.oauth as oauth
        importlib.reload(oauth)
        expected = Path(base) / "CivitaiArtistDiscovery"
        assert oauth.APP_DATA == expected, oauth.APP_DATA
        assert oauth.TOKEN_PATH == expected / "oauth_tokens.dpapi", oauth.TOKEN_PATH
finally:
    if original_local is not None:
        os.environ["LOCALAPPDATA"] = original_local
    if original_override is not None:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = original_override
    else:
        os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)

print({"freshInstall": True, "legacyFolderMoved": True, "bothPresentPrefersCurrent": True,
       "overrideWins": True, "oauthSharesResolver": True})

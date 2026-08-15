"""The data folder has one definition, and a rename never orphans a user's archives."""

import importlib
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def reload_paths(local_app_data, override=None, app_root=None):
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    if override is None:
        os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)
    else:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = str(override)
    import discovery.paths as paths
    paths = importlib.reload(paths)
    if app_root is not None:
        paths.application_root = lambda: Path(app_root)
    return paths


original_local = os.environ.get("LOCALAPPDATA")
original_override = os.environ.get("CIVITAI_HISTORY_DATA_DIR")
try:
    # A fresh machine uses data/ beside the application without touching LocalAppData.
    with tempfile.TemporaryDirectory(prefix="loc-fresh-") as base:
        app = Path(base) / "portable-app"
        app.mkdir()
        paths = reload_paths(base, app_root=app)
        root = paths.data_root()
        assert root == app / "data", root
        assert not root.exists(), "resolving the path should not create it"

    # An existing installation is moved into portable storage, with its contents.
    with tempfile.TemporaryDirectory(prefix="loc-legacy-") as base:
        app = Path(base) / "portable-app"
        app.mkdir()
        paths = reload_paths(base, app_root=app)
        previous = Path(base) / "CivitaiArtistDiscovery"
        (previous / "history").mkdir(parents=True)
        (previous / "history" / "history.sqlite3").write_bytes(b"archive")
        (previous / "oauth_tokens.dpapi").write_bytes(b"token")
        root = paths.data_root()
        assert root == app / "data", root
        assert not previous.exists(), "the previous data folder was left behind"
        assert (root / "history" / "history.sqlite3").read_bytes() == b"archive"
        assert (root / "oauth_tokens.dpapi").read_bytes() == b"token"

    # Existing portable data wins and old data is not merged over it.
    with tempfile.TemporaryDirectory(prefix="loc-both-") as base:
        app = Path(base) / "portable-app"
        app.mkdir()
        paths = reload_paths(base, app_root=app)
        previous, portable = Path(base) / "CivitaiArtistDiscovery", app / "data"
        previous.mkdir(); portable.mkdir(parents=True)
        (previous / "marker").write_bytes(b"old")
        assert paths.data_root() == portable
        assert (previous / "marker").exists()

    # An explicit override always wins and never triggers a move.
    with tempfile.TemporaryDirectory(prefix="loc-override-") as base:
        chosen = Path(base) / "elsewhere"
        app = Path(base) / "portable-app"
        app.mkdir()
        paths = reload_paths(base, override=chosen, app_root=app)
        legacy = Path(base) / "CivitaiArtistHistory"
        legacy.mkdir()
        assert paths.data_root() == chosen
        assert legacy.exists(), "an override must not move anything"

    # Every module that needs the folder asks the same resolver, which is what stops the
    # copies drifting the way the OAuth token path once did.
    with tempfile.TemporaryDirectory(prefix="loc-shared-") as base:
        app = Path(base) / "portable-app"
        app.mkdir()
        reload_paths(base, app_root=app)
        import discovery.oauth as oauth
        importlib.reload(oauth)
        expected = app / "data"
        assert oauth.APP_DATA == expected, oauth.APP_DATA
        assert oauth.TOKEN_PATH == expected / "oauth_tokens.dpapi", oauth.TOKEN_PATH
finally:
    if original_local is not None:
        os.environ["LOCALAPPDATA"] = original_local
    if original_override is not None:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = original_override
    else:
        os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)

print({"freshPortableInstall": True, "existingInstallMoved": True,
       "portableFolderWins": True, "overrideWins": True, "oauthSharesResolver": True})

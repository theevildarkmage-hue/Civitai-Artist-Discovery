"""Updater versioning, package safety, portable preservation, and rollback."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discovery.updater as updater


assert updater.is_newer("0.3.2-beta.1", "0.3.1-beta.9")
assert updater.is_newer("0.3.2", "0.3.2-beta.9")
assert updater.is_newer("1.0.0-beta.10", "1.0.0-beta.2")
assert not updater.is_newer("0.3.1-beta.1", "0.3.1-beta.1")
assert not updater.is_newer("0.3.1-beta.1", "0.3.1")


def release(version, *, draft=False, digest=None):
    name = f"CivitaiArtistDiscovery-{version}.zip"
    return {"tag_name": f"v{version}", "name": f"Release {version}", "body": "Changes",
            "draft": draft, "prerelease": "beta" in version,
            "published_at": "2026-08-21T12:00:00Z",
            "html_url": f"https://github.com/{updater.REPOSITORY}/releases/tag/v{version}",
            "assets": [{"name": name, "state": "uploaded", "size": 123,
                        "digest": "sha256:" + "a" * 64 if digest is None else digest,
                        "browser_download_url":
                            f"https://github.com/{updater.REPOSITORY}/releases/download/v{version}/{name}"}]}


picked = updater.select_release([
    release("0.3.2-beta.2"), release("0.3.2-beta.10"), release("9.0.0", draft=True),
    release("2.0.0", digest=""),
], "0.3.1-beta.1")
assert picked["version"] == "0.3.2-beta.10" and picked["sha256"] == "a" * 64, picked

try:
    bad = release("0.3.3")
    bad["assets"][0]["browser_download_url"] = "https://example.com/update.zip"
    updater.select_release([bad], "0.3.1")
    raise AssertionError("an untrusted asset host was accepted")
except ValueError:
    pass


with tempfile.TemporaryDirectory(prefix="civitai-updater-") as temporary:
    base = Path(temporary)
    install = base / "CivitaiArtistDiscovery"
    data = install / "data"
    stage = data / "update" / "staged" / "0.4.0" / "CivitaiArtistDiscovery"
    install.mkdir(); data.mkdir(); stage.mkdir(parents=True)
    (install / "CivitaiArtistDiscovery.exe").write_text("old-exe", encoding="utf-8")
    (install / "_internal").mkdir(); (install / "_internal" / "old.dll").write_text("old", encoding="utf-8")
    (install / "user-note.txt").write_text("mine", encoding="utf-8")
    database = data / "history.sqlite3"; database.write_text("personal-data", encoding="utf-8")
    (stage / "CivitaiArtistDiscovery.exe").write_text("new-exe", encoding="utf-8")
    (stage / "_internal").mkdir(); (stage / "_internal" / "new.dll").write_text("new", encoding="utf-8")
    # A helper-mode launch creates a nested data folder in staging; it must not ship.
    (stage / "data").mkdir(); (stage / "data" / "empty.sqlite3").write_text("wrong", encoding="utf-8")
    result = data / "update" / "result.json"
    config = data / "update" / "apply.json"
    config.write_text(json.dumps({
        "version": "0.4.0", "parentPid": 999,
        "installRoot": str(install), "stagedRoot": str(stage), "dataRoot": str(data),
        "executableName": "CivitaiArtistDiscovery.exe",
        "backupRoot": str(data / "update" / "backup" / "0.3.1"),
        "resultPath": str(result),
    }), encoding="utf-8")
    applied = updater.apply_staged_update(config, launch=False, wait_for_process=lambda pid: None)
    assert applied["state"] == "installed"
    assert (install / "CivitaiArtistDiscovery.exe").read_text() == "new-exe"
    assert (install / "_internal" / "new.dll").read_text() == "new"
    assert not (install / "_internal" / "old.dll").exists()
    assert database.read_text() == "personal-data"
    assert (install / "user-note.txt").read_text() == "mine"
    assert not (install / "data" / "empty.sqlite3").exists()
    # Once the replacement build has successfully started, staged binaries, the old
    # package backup, and the archive are temporary. The receipt remains for the UI.
    update_root = data / "update"
    (update_root / "CivitaiArtistDiscovery-0.4.0.zip").write_bytes(b"archive")
    manager = updater.UpdateManager(data, install, "0.4.0",
                                    executable=install / "CivitaiArtistDiscovery.exe",
                                    frozen=True)
    manager.schedule_success_cleanup(delay=0)
    deadline = time.monotonic() + 3
    while (update_root / "staged").exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert result.exists()
    assert not (update_root / "staged").exists()
    assert not (update_root / "backup").exists()
    assert not (update_root / "CivitaiArtistDiscovery-0.4.0.zip").exists()

with tempfile.TemporaryDirectory(prefix="civitai-updater-rollback-") as temporary:
    base = Path(temporary); install = base / "app"; data = install / "data"
    stage = data / "update" / "staged" / "new" / "app"
    install.mkdir(); data.mkdir(); stage.mkdir(parents=True)
    (install / "app.exe").write_text("old", encoding="utf-8")
    (install / "_internal").mkdir(); (install / "_internal" / "old.dll").write_text("old", encoding="utf-8")
    (stage / "app.exe").write_text("new", encoding="utf-8")
    (stage / "_internal").mkdir(); (stage / "_internal" / "new.dll").write_text("new", encoding="utf-8")
    result = data / "update" / "result.json"; config = data / "update" / "apply.json"
    config.write_text(json.dumps({"version": "new", "parentPid": 0,
        "installRoot": str(install), "stagedRoot": str(stage), "dataRoot": str(data),
        "executableName": "app.exe", "backupRoot": str(data / "update" / "backup"),
        "resultPath": str(result)}), encoding="utf-8")
    original_copy = updater._copy_entry
    def fail_internal(source, target):
        if source == stage / "_internal":
            raise OSError("simulated locked file")
        return original_copy(source, target)
    updater._copy_entry = fail_internal
    try:
        updater.apply_staged_update(config, launch=False, wait_for_process=lambda pid: None)
        raise AssertionError("simulated install failure did not fail")
    except OSError:
        pass
    finally:
        updater._copy_entry = original_copy
    assert (install / "app.exe").read_text() == "old"
    assert (install / "_internal" / "old.dll").read_text() == "old"
    assert json.loads(result.read_text())["rolledBack"] is True

with tempfile.TemporaryDirectory(prefix="civitai-updater-zip-") as temporary:
    root = Path(temporary); archive = root / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("CivitaiArtistDiscovery/CivitaiArtistDiscovery.exe", "new")
        package.writestr("CivitaiArtistDiscovery/_internal/runtime.dll", "runtime")
    staged = updater._extract_verified(archive, root / "extract", "CivitaiArtistDiscovery.exe")
    assert staged.name == "CivitaiArtistDiscovery"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    received = updater._download(archive.as_uri(), root / "copy.zip", archive.stat().st_size,
                                 lambda done, total: None)
    assert received == digest

    unsafe = root / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as package:
        package.writestr("../escape.txt", "bad")
    try:
        updater._extract_verified(unsafe, root / "unsafe", "CivitaiArtistDiscovery.exe")
        raise AssertionError("zip traversal was accepted")
    except RuntimeError as error:
        assert "unsafe path" in str(error)

print({"semverPrereleases": True, "signedAssetRequired": True,
       "portableDataPreserved": True, "unknownFilesPreserved": True,
       "rollbackRestoresOldBuild": True, "zipTraversalRejected": True,
       "downloadHashCalculated": True, "temporaryFilesCleaned": True})

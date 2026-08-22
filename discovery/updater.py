"""Portable GitHub release updates with verified downloads and rollback.

The application is a PyInstaller folder build, so the running executable cannot replace
its own files on Windows.  The downloaded *new* executable is launched from a staging
folder in helper mode after the server has stopped.  It copies its package into the real
application folder while leaving the existing portable ``data/`` directory untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile


REPOSITORY = "theevildarkmage-hue/Civitai-Artist-Discovery"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"
CHECK_INTERVAL = timedelta(hours=24)
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_EXTRACTED_BYTES = 900 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
VERSION_PATTERN = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_MISSING = object()


def _read_json(path: Path, default=_MISSING):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is _MISSING else default


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def version_key(value: str) -> tuple:
    """Return a SemVer-compatible key for the versions this project publishes."""
    match = VERSION_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError(f"Invalid application version: {value}")
    core = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    prerelease = match.group("pre")
    if prerelease is None:
        return (*core, 1, ())
    parts = []
    for part in prerelease.split("."):
        # Numeric prerelease identifiers sort before text identifiers.
        parts.append((0, int(part)) if part.isdigit() else (1, part.casefold()))
    return (*core, 0, tuple(parts))


def is_newer(candidate: str, current: str) -> bool:
    try:
        return version_key(candidate) > version_key(current)
    except ValueError:
        return False


def _safe_download_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or ""))
    expected_prefix = f"/{REPOSITORY}/releases/download/"
    if parsed.scheme != "https" or parsed.hostname != "github.com" \
            or not parsed.path.startswith(expected_prefix):
        raise ValueError("GitHub returned an unexpected release download URL")
    return parsed.geturl()


def select_release(releases: object, current_version: str) -> dict | None:
    """Choose the newest published release containing this app's verified ZIP."""
    choices = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name") or "").removeprefix("v")
        if not is_newer(tag, current_version):
            continue
        expected = f"CivitaiArtistDiscovery-{tag}.zip"
        assets = [asset for asset in release.get("assets") or []
                  if isinstance(asset, dict) and asset.get("state") == "uploaded"]
        archive = next((asset for asset in assets if asset.get("name") == expected), None)
        if not archive:
            continue
        digest = str(archive.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            # Updating executable code without a digest is never an acceptable fallback.
            continue
        choices.append((version_key(tag), {
            "version": tag,
            "tag": str(release.get("tag_name") or f"v{tag}"),
            "name": str(release.get("name") or f"Civitai Artist Discovery {tag}"),
            "notes": str(release.get("body") or "No release notes were provided."),
            "publishedAt": release.get("published_at"),
            "pageUrl": str(release.get("html_url") or ""),
            "assetName": expected,
            "assetUrl": _safe_download_url(archive.get("browser_download_url")),
            "assetSize": int(archive.get("size") or 0),
            "sha256": digest.split(":", 1)[1].lower(),
            "prerelease": bool(release.get("prerelease")),
        }))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def _open_json(url: str, timeout: int = 15):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Civitai-Artist-Discovery-Updater",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if int(response.headers.get("Content-Length") or 0) > 5 * 1024 * 1024:
            raise RuntimeError("GitHub returned an unexpectedly large update response")
        return json.loads(response.read(5 * 1024 * 1024 + 1))


def _download(url: str, target: Path, expected_size: int, progress) -> str:
    request = urllib.request.Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "Civitai-Artist-Discovery-Updater",
    })
    digest = hashlib.sha256()
    received = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as output:
        declared = int(response.headers.get("Content-Length") or expected_size or 0)
        if declared > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("The update package is unexpectedly large")
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            received += len(block)
            if received > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("The update package exceeded the safe size limit")
            digest.update(block)
            output.write(block)
            progress(received, declared or expected_size)
        output.flush()
        os.fsync(output.fileno())
    if expected_size and received != expected_size:
        raise RuntimeError(f"The update download was incomplete ({received} of {expected_size} bytes)")
    return digest.hexdigest()


def _extract_verified(archive: Path, destination: Path, executable_name: str) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise RuntimeError("The update package contains too many files")
        if sum(member.file_size for member in members) > MAX_EXTRACTED_BYTES:
            raise RuntimeError("The unpacked update is unexpectedly large")
        for member in members:
            candidate = (destination / member.filename).resolve()
            if candidate != root and root not in candidate.parents:
                raise RuntimeError("The update package contains an unsafe path")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("The update package contains an unsupported symbolic link")
        package.extractall(destination)
    executables = [path for path in destination.rglob(executable_name)
                   if path.is_file() and path.parent.name == "CivitaiArtistDiscovery"]
    if len(executables) != 1:
        raise RuntimeError("The update package does not contain the expected application")
    return executables[0].parent


def _wait_for_process(pid: int, timeout: float = 90) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE, WAIT_OBJECT_0 = 0x00100000, 0
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return
        try:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if ctypes.windll.kernel32.WaitForSingleObject(handle, remaining) != WAIT_OBJECT_0:
                raise TimeoutError("The running application did not close in time")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(.2)
    raise TimeoutError("The running application did not close in time")


def _copy_entry(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _remove_entry(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def apply_staged_update(config_path: Path, *, launch: bool = True,
                        wait_for_process=_wait_for_process) -> dict:
    """Apply one staged package. This function is independently regression-tested."""
    config_path = Path(config_path).resolve()
    config = _read_json(config_path)
    install_root = Path(config["installRoot"]).resolve()
    staged_root = Path(config["stagedRoot"]).resolve()
    data_root = Path(config["dataRoot"]).resolve()
    executable_name = str(config["executableName"])
    result_path = Path(config["resultPath"]).resolve()
    backup_root = Path(config["backupRoot"]).resolve()
    launch = launch and config.get("relaunch", True) is not False
    if install_root == staged_root or not staged_root.is_dir() or not install_root.is_dir():
        raise RuntimeError("The staged update paths are invalid")
    if data_root.parent != install_root:
        raise RuntimeError("The portable data folder is outside the application folder")
    # The helper executable must come from our own portable update staging area. This
    # prevents a modified apply.json from turning the updater into an arbitrary copier.
    if staged_root == data_root or data_root not in staged_root.parents:
        raise RuntimeError("The staged update is outside the portable data folder")
    staged_executable = staged_root / executable_name
    if not staged_executable.is_file():
        raise RuntimeError("The staged executable is missing")
    wait_for_process(int(config.get("parentPid") or 0))
    targets = [entry for entry in staged_root.iterdir()
               if entry.name.casefold() != "data"]
    if not targets:
        raise RuntimeError("The staged package is empty")
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.mkdir(parents=True)
    changed = []
    try:
        for source in targets:
            target = install_root / source.name
            backup = backup_root / source.name
            if target.exists():
                _copy_entry(target, backup)
            _remove_entry(target)
            changed.append(source.name)
            _copy_entry(source, target)
        result = {"state": "installed", "version": config.get("version"),
                  "installedAt": _now(), "rolledBack": False}
        _write_json(result_path, result)
    except Exception as error:
        for name in reversed(changed):
            target = install_root / name
            _remove_entry(target)
            backup = backup_root / name
            if backup.exists():
                _copy_entry(backup, target)
        result = {"state": "failed", "version": config.get("version"),
                  "failedAt": _now(), "rolledBack": True, "error": str(error)[:500]}
        _write_json(result_path, result)
        raise
    if launch:
        command = [str(install_root / executable_name), "--updated-from", str(config.get("version") or "")]
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, cwd=install_root, close_fds=True, creationflags=flags)
    return result


class UpdateManager:
    def __init__(self, data_root: Path, install_root: Path, current_version: str,
                 executable: Path | None = None, *, frozen: bool | None = None):
        self.root = Path(data_root) / "update"
        self.install_root = Path(install_root).resolve()
        self.current_version = current_version
        self.executable = Path(executable or sys.executable).resolve()
        self.supported = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
        self.cache_path = self.root / "check.json"
        self.result_path = self.root / "result.json"
        self.lock = threading.RLock()
        self.job = {"phase": "idle", "downloaded": 0, "total": 0, "error": None}
        self.release = None
        self._load_cache()

    def _load_cache(self) -> None:
        cached = _read_json(self.cache_path)
        release = cached.get("release") if isinstance(cached, dict) else None
        self.release = release if isinstance(release, dict) \
            and is_newer(release.get("version"), self.current_version) else None

    def _cache_fresh(self) -> bool:
        value = _read_json(self.cache_path).get("checkedAt")
        try:
            checked = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - checked < CHECK_INTERVAL
        except (TypeError, ValueError):
            return False

    def status(self) -> dict:
        with self.lock:
            job = dict(self.job)
            release = dict(self.release) if self.release else None
        result = _read_json(self.result_path, None)
        return {"supported": self.supported, "currentVersion": self.current_version,
                "available": bool(release), "release": release, "job": job,
                "lastResult": result if isinstance(result, dict) else None}

    def start_check(self, force: bool = False) -> dict:
        with self.lock:
            if self.job["phase"] in {"checking", "downloading", "preparing"}:
                return self.status()
            if not force and self._cache_fresh():
                return self.status()
            self.job = {"phase": "checking", "downloaded": 0, "total": 0, "error": None}
        threading.Thread(target=self._check_worker, daemon=True, name="github-update-check").start()
        return self.status()

    def _check_worker(self) -> None:
        try:
            release = select_release(_open_json(RELEASES_URL), self.current_version)
            _write_json(self.cache_path, {"checkedAt": _now(), "release": release})
            with self.lock:
                self.release = release
                self.job = {"phase": "available" if release else "idle",
                            "downloaded": 0, "total": 0, "error": None}
        except Exception as error:
            with self.lock:
                self.job = {"phase": "error", "downloaded": 0, "total": 0,
                            "error": f"Could not check GitHub for updates: {error}"[:500]}

    def start_download(self) -> dict:
        if not self.supported:
            raise RuntimeError("Automatic installation is available in packaged builds")
        with self.lock:
            if not self.release:
                raise RuntimeError("No newer update is available")
            if self.job["phase"] in {"downloading", "preparing"}:
                return self.status()
            release = dict(self.release)
            self.job = {"phase": "downloading", "downloaded": 0,
                        "total": release["assetSize"], "error": None}
        threading.Thread(target=self._download_worker, args=(release,), daemon=True,
                         name="github-update-download").start()
        return self.status()

    def _download_worker(self, release: dict) -> None:
        version = release["version"]
        part = self.root / f"{release['assetName']}.part"
        archive = self.root / release["assetName"]
        extract = self.root / "staged" / version
        try:
            def progress(received, total):
                with self.lock:
                    self.job.update({"downloaded": received, "total": total})
            digest = _download(release["assetUrl"], part, release["assetSize"], progress)
            if digest != release["sha256"]:
                raise RuntimeError("The downloaded update failed SHA-256 verification")
            part.replace(archive)
            with self.lock:
                self.job["phase"] = "preparing"
            staged_root = _extract_verified(archive, extract, self.executable.name)
            with self.lock:
                self.job = {"phase": "ready", "downloaded": release["assetSize"],
                            "total": release["assetSize"], "error": None,
                            "stagedRoot": str(staged_root)}
        except Exception as error:
            try:
                if part.exists(): part.unlink()
            except OSError:
                pass
            with self.lock:
                self.job = {"phase": "error", "downloaded": 0,
                            "total": release.get("assetSize", 0),
                            "error": f"Could not prepare the update: {error}"[:500]}

    def helper_command(self) -> list[str]:
        with self.lock:
            if not self.release or self.job.get("phase") != "ready":
                raise RuntimeError("Download and verify the update first")
            release, staged_root = dict(self.release), Path(self.job["stagedRoot"]).resolve()
        helper_executable = staged_root / self.executable.name
        if not helper_executable.is_file():
            raise RuntimeError("The staged update helper is missing")
        config = {
            "version": release["version"], "parentPid": os.getpid(),
            "installRoot": str(self.install_root), "stagedRoot": str(staged_root),
            "dataRoot": str(self.root.parent.resolve()),
            "executableName": self.executable.name,
            "backupRoot": str((self.root / "backup" / self.current_version).resolve()),
            "resultPath": str(self.result_path.resolve()),
            "relaunch": True,
        }
        config_path = self.root / "apply.json"
        _write_json(config_path, config)
        return [str(helper_executable), "--apply-update", str(config_path)]

    def clear_result(self) -> None:
        try:
            self.result_path.unlink()
        except FileNotFoundError:
            pass

    def schedule_success_cleanup(self, delay: float = 8) -> None:
        """Remove verified install leftovers after the new build has started safely.

        The result receipt is deliberately retained until the browser acknowledges it.
        Waiting also gives the staged helper time to exit before Windows releases every
        file in the extracted application folder.
        """
        result = _read_json(self.result_path, None)
        if not isinstance(result, dict) or result.get("state") != "installed" \
                or result.get("version") != self.current_version:
            return

        def cleanup() -> None:
            time.sleep(max(0, delay))
            for attempt in range(5):
                try:
                    for directory in (self.root / "staged", self.root / "backup"):
                        if directory.exists():
                            shutil.rmtree(directory)
                    for path in self.root.iterdir() if self.root.exists() else []:
                        if path.name == self.result_path.name or path.name == self.cache_path.name:
                            continue
                        if path.name == "apply.json" or path.suffix in {".zip", ".part"}:
                            _remove_entry(path)
                    return
                except OSError:
                    if attempt == 4:
                        return
                    time.sleep(2)

        threading.Thread(target=cleanup, daemon=True,
                         name="update-success-cleanup").start()

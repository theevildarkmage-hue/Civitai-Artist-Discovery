"""Exercise a built executable's replacement-helper mode in a disposable install."""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


if len(sys.argv) != 2:
    raise SystemExit("usage: python scripts/validate_packaged_updater.py <built-app-folder>")

package = Path(sys.argv[1]).resolve()
executable_name = "CivitaiArtistDiscovery.exe"
if not (package / executable_name).is_file():
    raise SystemExit(f"built executable not found in {package}")

with tempfile.TemporaryDirectory(prefix="civitai-packaged-update-") as temporary:
    base = Path(temporary)
    install = base / "CivitaiArtistDiscovery"
    shutil.copytree(package, install)
    data = install / "data"
    stage = data / "update" / "staged" / "test-version" / "CivitaiArtistDiscovery"
    shutil.copytree(package, stage)
    personal = data / "personal.sqlite3"
    personal.write_bytes(b"portable-user-data")
    unrelated = install / "my-notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    result = data / "update" / "result.json"
    config = data / "update" / "apply.json"
    config.write_text(json.dumps({
        "version": "test-version", "parentPid": 0,
        "installRoot": str(install), "stagedRoot": str(stage),
        "dataRoot": str(data), "executableName": executable_name,
        "backupRoot": str(data / "update" / "backup" / "old-version"),
        "resultPath": str(result), "relaunch": False,
    }), encoding="utf-8")

    process = subprocess.Popen([str(stage / executable_name), "--apply-update", str(config)],
                               cwd=stage)
    deadline = time.monotonic() + 90
    while not result.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(.1)
    process.wait(timeout=max(1, deadline - time.monotonic()))
    if process.returncode:
        error_log = stage / "data" / "error.log"
        raise AssertionError(error_log.read_text(encoding="utf-8") if error_log.exists()
                             else f"helper exited with {process.returncode}")
    receipt = json.loads(result.read_text(encoding="utf-8"))
    assert receipt["state"] == "installed" and receipt["rolledBack"] is False
    assert personal.read_bytes() == b"portable-user-data"
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert (install / executable_name).is_file()
    assert (install / "_internal").is_dir()
    assert not (data / "data").exists()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    application = subprocess.Popen([str(install / executable_name), "--no-browser",
                                    "--port", str(port)], cwd=install)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/update/status",
                                            timeout=2) as response:
                    update_status = json.load(response)
                break
            except Exception:
                if time.monotonic() >= deadline or application.poll() is not None:
                    raise
                time.sleep(.1)
        assert update_status["supported"] is True
        close = urllib.request.Request(f"http://127.0.0.1:{port}/api/app/close",
                                       data=b"{}", method="POST",
                                       headers={"Content-Type": "application/json"})
        urllib.request.urlopen(close, timeout=3).close()
        application.wait(timeout=15)
    finally:
        if application.poll() is None:
            application.terminate()
            application.wait(timeout=10)
    print({"packagedHelperRan": True, "portableDataPreserved": True,
           "unrelatedFilePreserved": True, "packageReplaced": True,
           "unexpectedRelaunch": False, "packagedServerStarted": True,
           "packagedUpdaterEnabled": True})

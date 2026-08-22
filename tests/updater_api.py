"""Update preference and source-build API behavior."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PORT = 8897


def request(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    call = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                  method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(call, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


with tempfile.TemporaryDirectory(prefix="civitai-update-api-") as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                status, value = request("/api/update/status")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.1)
        assert status == 200 and value["enabled"] is True
        assert value["supported"] is False and value["currentVersion"]
        assert value["lastResult"] is None
        status, value = request("/api/settings", "POST", {"checkForUpdates": False})
        assert status == 200 and value["checkForUpdates"] is False
        status, value = request("/api/update/check", "POST", {})
        assert status == 409 and "disabled" in value["error"].lower()
        status, value = request("/api/settings", "POST", {"checkForUpdates": True})
        assert status == 200 and value["checkForUpdates"] is True
        status, value = request("/api/update/status")
        assert status == 200 and value["enabled"] is True
        print({"preferencePersists": True, "disabledCheckRejected": True,
               "sourceInstallIdentified": True, "statusShapeValid": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

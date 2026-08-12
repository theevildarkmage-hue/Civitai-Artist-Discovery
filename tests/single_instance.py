"""Verify a second launch exits while the first local instance remains healthy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PORT = 8879

with tempfile.TemporaryDirectory(prefix="civitai-instance-") as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    first = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=ROOT, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1): break
            except OSError: time.sleep(.1)
        second = subprocess.run([sys.executable, "server.py", "--port", "0", "--no-browser"], cwd=ROOT, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        assert second.returncode == 0 and first.poll() is None
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=2) as response:
            assert response.status == 200
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=2) as response: assert json.load(response)["closing"]
        first.wait(timeout=10)
        assert not (Path(temporary) / "running-instance.json").exists()
    finally:
        if first.poll() is None: first.terminate(); first.wait(timeout=10)

print({"secondLaunchExited": True, "firstInstanceHealthy": True, "instanceFileCleaned": True})

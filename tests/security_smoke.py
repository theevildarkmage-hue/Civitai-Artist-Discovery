"""Fast checks for the local server's security boundaries."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
PORT = 8878


with tempfile.TemporaryDirectory(prefix="civitai-security-") as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT), "--no-browser"], cwd=ROOT, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
                connection.request("GET", "/api/history/config", headers={"Host": "127.0.0.1"})
                if connection.getresponse().status == 200: break
            except OSError:
                time.sleep(.1)
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
        connection.request("GET", "/api/history/config", headers={"Host": "attacker.example"})
        assert connection.getresponse().status == 403
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
        connection.request("GET", "/api/history/config", headers={"Host": f"127.0.0.1:{PORT}"})
        response = connection.getresponse()
        config = json.loads(response.read())
        assert response.status == 200 and "dataDirectory" not in config
    finally:
        process.terminate()
        process.wait(timeout=10)

    exposed = subprocess.run([sys.executable, "server.py", "--host", "0.0.0.0", "--no-browser"], cwd=ROOT, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    assert exposed.returncode != 0

print({"hostHeaderRejected": True, "dataPathHidden": True, "networkBindRejected": True})

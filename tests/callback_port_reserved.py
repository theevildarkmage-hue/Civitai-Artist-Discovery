"""The app must never occupy the port Civitai redirects sign-in to.

The redirect URI is registered against one fixed port. If the app server binds it, the
callback listener cannot, and Civitai's redirect lands on the app — which answers 404
and strands the user mid-login with no way to tell what went wrong.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from discovery.oauth import CALLBACK_PORT, REDIRECT_URI

# The redirect URI and the reserved port must agree; a drift between them would make
# this test pass while sign-in still broke.
assert f":{CALLBACK_PORT}/" in REDIRECT_URI, (CALLBACK_PORT, REDIRECT_URI)


def bound_port(requested: str) -> int:
    temporary = tempfile.mkdtemp(prefix="civitai-port-guard-")
    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, "-B", str(ROOT / "server.py"),
                                "--port", requested, "--no-browser"], cwd=ROOT, env=env)
    try:
        instance = Path(temporary) / "running-instance.json"
        deadline = time.monotonic() + 30
        while not instance.exists():
            if time.monotonic() > deadline:
                raise AssertionError(f"server never reported a URL for --port {requested}")
            time.sleep(0.25)
        return int(json.loads(instance.read_text(encoding="utf-8"))["url"].rsplit(":", 1)[1])
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


# Asking for the reserved port explicitly must not get it.
reserved = bound_port(str(CALLBACK_PORT))
assert reserved != CALLBACK_PORT, reserved

# An ordinary request is still honoured exactly, so the guard is not blanket-rerolling.
ordinary = bound_port("8771")
assert ordinary == 8771, ordinary

print({"reservedPortRefused": True, "fellBackTo": reserved,
       "ordinaryPortHonoured": ordinary, "redirectMatchesReservedPort": True})

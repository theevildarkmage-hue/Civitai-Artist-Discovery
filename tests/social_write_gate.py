"""Follows and reactions require Civitai to have granted write access.

Sign-in now asks for follows and reactions up front, so consent happens once on Civitai's
own screen and there is no separate in-app switch. That makes the grant the only gate —
and it has to actually be enforced. An alpha tester previously managed to follow through
a connection that should not have allowed it, so the refusal is tested, not assumed.
"""

import importlib
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
sys.path.insert(0, str(ROOT))
PORT = 8898


def follow(port):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/follow",
        data=json.dumps({"userId": 123, "username": "someone", "following": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=15)
        return 200
    except urllib.error.HTTPError as error:
        return error.code


original = os.environ.get("CIVITAI_HISTORY_DATA_DIR")
with tempfile.TemporaryDirectory(prefix="civitai-write-gate-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.oauth as oauth
    oauth = importlib.reload(oauth)

    def write_token(scope, extra=None):
        payload = {"access_token": "a", "refresh_token": "r", "expires_at": 2 ** 31,
                   "client_id": oauth.client_id(), "scope": scope,
                   "identity": {"id": 7, "username": "tester"}, **(extra or {})}
        oauth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        oauth.TOKEN_PATH.write_bytes(oauth._crypt(json.dumps(payload).encode(), True))

    WRITE = 524321   # UserRead | MediaRead | SocialWrite
    READ = 33        # UserRead | MediaRead

    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        def state():
            return json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/auth-status", timeout=10).read())

        # Granted write access: following is allowed.
        write_token(WRITE)
        granted = state()
        assert granted["scopeGrantsWrite"] is True, granted
        assert granted["socialWrite"] is True, granted
        assert follow(PORT) != 403, "a granted connection was refused"

        # Signed in, but Civitai withheld write access: every write must be refused, and
        # the app must say so rather than presenting buttons that quietly do nothing.
        write_token(READ)
        refused = state()
        assert refused["connected"] is True, refused
        assert refused["scopeGrantsWrite"] is False and refused["socialWrite"] is False, refused
        assert follow(PORT) == 403, "a read-only grant was allowed to follow"

        # Tokens written before the separate opt-in was removed carry a stale flag. It is
        # ignored now: the grant decides, so an old token does not stay crippled.
        write_token(WRITE, {"social_opt_in": False})
        legacy = state()
        assert legacy["socialWrite"] is True, legacy
        assert follow(PORT) != 403, "a pre-existing session was left unable to follow"

    finally:
        process.terminate(); process.wait(timeout=15)

if original is not None:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = original
else:
    os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)

print({"grantAllowsWrites": True, "withheldGrantBlocksWrites": True,
       "staleOptInFlagIgnored": True})

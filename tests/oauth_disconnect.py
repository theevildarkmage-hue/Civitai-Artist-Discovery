"""Disconnecting revokes the authorization at Civitai, not just the local copy."""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

original_override = os.environ.get("CIVITAI_HISTORY_DATA_DIR")
with tempfile.TemporaryDirectory(prefix="civitai-disconnect-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.oauth as oauth
    oauth = importlib.reload(oauth)

    def write_token(client, **extra):
        payload = {"access_token": "access-value", "refresh_token": "refresh-value",
                   "expires_at": 2 ** 31, "client_id": client, "scope": 33,
                   "identity": {"id": 7, "username": "tester"}, **extra}
        oauth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        oauth.TOKEN_PATH.write_bytes(oauth._crypt(json.dumps(payload).encode(), True))

    calls = []
    real_revoke = oauth._revoke
    oauth._revoke = lambda token, hint, client: (calls.append((token, hint, client)), True)[1]

    # A token belonging to the configured application is revoked before it is deleted.
    write_token(oauth.client_id())
    oauth.disconnect()
    assert not oauth.TOKEN_PATH.exists(), "the local token survived disconnect"
    assert [hint for _, hint, _ in calls] == ["refresh_token", "access_token"], calls
    assert [token for token, _, _ in calls] == ["refresh-value", "access-value"], calls
    assert all(client == oauth.client_id() for _, _, client in calls), calls

    # A token issued by a different application is not sent to the current one's revoke.
    calls.clear()
    write_token("a-different-application")
    oauth.disconnect()
    assert calls == [], calls
    assert not oauth.TOKEN_PATH.exists()

    # A failed revocation must not strand the user with a token they cannot remove.
    calls.clear()
    oauth._revoke = lambda token, hint, client: (calls.append(hint), False)[1]
    write_token(oauth.client_id())
    oauth.disconnect()
    assert calls == ["refresh_token", "access_token"], calls
    assert not oauth.TOKEN_PATH.exists(), "a failed revocation blocked local disconnect"

    # Disconnecting with nothing stored is harmless.
    calls.clear()
    oauth.disconnect()
    assert calls == []

    # Changing the application also revokes, since the old grant is no longer reachable.
    calls.clear()
    oauth._revoke = lambda token, hint, client: (calls.append(hint), True)[1]
    write_token(oauth.client_id())
    oauth.set_client_id("99999999-8888-7777-6666-555555555555")
    assert calls == ["refresh_token", "access_token"], calls
    assert not oauth.TOKEN_PATH.exists()

    oauth._revoke = real_revoke

if original_override is not None:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = original_override
else:
    os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)

print({"revokesBeforeDelete": True, "onlyOwnApplication": True,
       "failureStillDisconnects": True, "noTokenIsHarmless": True,
       "switchingApplicationRevokes": True})

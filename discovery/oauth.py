"""OAuth 2.0 + PKCE with operating-system-protected per-user tokens."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

from .paths import data_root


# The OAuth application this build ships with. Civitai's own documentation describes
# registering an application per integration, so shipping one is the expected pattern and
# spares every user a setup step.
#
# Setting this to "" is the switch: the app then ships with no application of its own and
# each user must register one before connecting. Everything else — storage, validation, the
# setup panel, the disabled Connect button — already handles both modes, so no other change
# is needed. Worth flipping if the registration ever becomes a liability: every user's
# authorization runs through it, abuse is attributed to it, and rate limits may be pooled
# across everyone using it.
BUILTIN_CLIENT_ID = "b9e2edf5-4bf0-4381-8823-ed01603256c5"
AUTH_BASE = "https://auth.civitai.com/api/auth/oauth"
CALLBACK_PORT = 8765
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/oauth/callback"
SOCIAL_WRITE = 1 << 19
READ_SCOPE = 1 | 32  # UserRead | MediaRead
APP_DATA = data_root()
TOKEN_PATH = APP_DATA / "oauth_tokens.dpapi"
KEYRING_SERVICE = "CivitaiArtistDiscovery"
KEYRING_ACCOUNT = "oauth-tokens"
# An earlier build derived this path separately from the rest of the app and dropped the
# token in %LOCALAPPDATA% itself rather than the documented per-app folder. Keep the old
# location only to move it.
LEGACY_TOKEN_PATH = Path(os.environ.get("CIVITAI_HISTORY_DATA_DIR")
    or os.environ.get("LOCALAPPDATA") or Path.home() / ".civitai-artist-history") / "oauth_tokens.dpapi"
HEADERS = {"Accept": "application/json", "User-Agent": "CivitaiArtistDiscovery/1.0"}
CLIENT_PATH = APP_DATA / "oauth_client.json"


class OAuthSetupError(RuntimeError):
    """A failure the user can act on, so its message is shown rather than generalised."""


def _uses_dpapi() -> bool:
    return os.name == "nt"


def _uses_secret_service() -> bool:
    return sys.platform.startswith("linux")


def _unsupported_storage() -> OAuthSetupError:
    return OAuthSetupError(f"Secure OAuth storage is not supported on {sys.platform}. "
                           "Use Windows or a Linux desktop with Secret Service.")


def _stored_client_id() -> str:
    try:
        stored = json.loads(CLIENT_PATH.read_text(encoding="utf-8")).get("clientId")
        return stored.strip() if isinstance(stored, str) else ""
    except (OSError, ValueError):
        return ""


def client_id() -> str:
    """The Civitai application this install authorizes against.

    Order: an environment override for development, then one the user chose, then the
    application this build ships with. Empty means nothing is configured, which happens
    only when the build ships without one.
    """
    override = (os.environ.get("CIVITAI_OAUTH_CLIENT_ID") or "").strip()
    return override or _stored_client_id() or BUILTIN_CLIENT_ID


def client_info() -> dict:
    current = client_id()
    return {"clientId": current, "configured": bool(current), "redirectUri": REDIRECT_URI,
            "hasBuiltin": bool(BUILTIN_CLIENT_ID),
            "isCustom": bool(current) and current != BUILTIN_CLIENT_ID,
            "fromEnvironment": bool((os.environ.get("CIVITAI_OAUTH_CLIENT_ID") or "").strip())}


def set_client_id(value: str | None) -> dict:
    """Record which Civitai application to use, or clear it.

    Any stored authorization belongs to the previous application, so it is removed rather
    than left to fail confusingly on the next call.
    """
    APP_DATA.mkdir(parents=True, exist_ok=True)
    # Disconnect first, while the old application is still the configured one. Revoking a
    # grant requires naming the application that issued it, so switching before
    # disconnecting would leave the old authorization live on the user's Civitai account.
    disconnect()
    cleaned = (value or "").strip()
    if cleaned:
        temporary = CLIENT_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({"clientId": cleaned}), encoding="utf-8")
        os.replace(temporary, CLIENT_PATH)
    elif CLIENT_PATH.exists():
        CLIENT_PATH.unlink()
    return client_info()


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI credential storage is available on Windows only")
    source, keepalive = _blob(data); result = DATA_BLOB()
    function = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    args = (ctypes.byref(source), "Civitai OAuth tokens", None, None, None, 0, ctypes.byref(result)) if protect else (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result))
    if not function(*args): raise ctypes.WinError()
    try: return ctypes.string_at(result.pbData, result.cbData)
    finally: ctypes.windll.kernel32.LocalFree(result.pbData); del keepalive


def _linux_keyring():
    """Return a usable Secret Service keyring, never a plaintext fallback."""
    try:
        import keyring
    except ImportError as error:
        raise OAuthSetupError("Secure OAuth storage on Linux requires the 'keyring' "
                              "package. Install the project's requirements and try again.") from error
    try:
        backend = keyring.get_keyring()
    except Exception as error:  # noqa: BLE001
        raise OAuthSetupError("Linux Secret Service could not be opened. Make sure a "
                              "desktop keyring is installed and unlocked.") from error
    backend_name = f"{type(backend).__module__}.{type(backend).__name__}"
    if type(backend).__module__ != "keyring.backends.SecretService":
        raise OAuthSetupError("Secure OAuth storage needs the Linux Secret Service "
                              f"keyring; the active backend is {backend_name}.")
    return keyring


def _load_linux() -> dict:
    keyring = _linux_keyring()
    try:
        serialized = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception as error:  # noqa: BLE001
        raise OAuthSetupError("The Linux Secret Service keyring could not be read. "
                              "Make sure it is unlocked and try again.") from error
    if not serialized:
        raise RuntimeError("Civitai is not connected")
    try:
        return json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise OAuthSetupError("The saved Civitai authorization is invalid. Disconnect "
                              "the credential and connect again.") from error


def _save_linux(tokens: dict) -> None:
    keyring = _linux_keyring()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT,
                             json.dumps(tokens, separators=(",", ":")))
    except Exception as error:  # noqa: BLE001
        raise OAuthSetupError("The Linux Secret Service keyring could not save the "
                              "authorization. Make sure it is unlocked and try again.") from error


def _delete_linux() -> None:
    keyring = _linux_keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as error:  # noqa: BLE001
        raise OAuthSetupError("The Linux Secret Service keyring could not delete the "
                              "authorization. Make sure it is unlocked and try again.") from error


def _migrate_legacy_token() -> None:
    """Move a token saved by an earlier build into the documented data folder."""
    if TOKEN_PATH.exists() or LEGACY_TOKEN_PATH == TOKEN_PATH or not LEGACY_TOKEN_PATH.exists():
        return
    try:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        os.replace(LEGACY_TOKEN_PATH, TOKEN_PATH)
    except OSError:
        pass  # A connection that cannot be moved is simply re-established by the user.


def _load() -> dict:
    if _uses_secret_service():
        return _load_linux()
    if not _uses_dpapi():
        raise _unsupported_storage()
    _migrate_legacy_token()
    if not TOKEN_PATH.exists(): raise RuntimeError("Civitai is not connected")
    return json.loads(_crypt(TOKEN_PATH.read_bytes(), False).decode())


def _save(tokens: dict) -> None:
    if _uses_secret_service():
        _save_linux(tokens)
        return
    if not _uses_dpapi():
        raise _unsupported_storage()
    APP_DATA.mkdir(parents=True, exist_ok=True)
    temporary = TOKEN_PATH.with_suffix(".tmp")
    temporary.write_bytes(_crypt(json.dumps(tokens, separators=(",", ":")).encode(), True))
    os.replace(temporary, TOKEN_PATH)


def _post(name: str, values: dict) -> dict:
    request = urllib.request.Request(f"{AUTH_BASE}/{name}", data=urllib.parse.urlencode(values).encode(), method="POST",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response: return json.loads(response.read())


def _userinfo(token: str) -> dict:
    request = urllib.request.Request(f"{AUTH_BASE}/userinfo", headers={**HEADERS, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response: return json.loads(response.read())


class Callback(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth/callback": self.send_error(404); return
        self.server.result = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}  # type: ignore[attr-defined]
        body = b"<!doctype html><title>Civitai connected</title><style>body{background:#111;color:#eee;font:20px system-ui;display:grid;place-items:center;height:90vh}</style><h1>Connected. You may close this tab.</h1>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): return


def login(timeout: int = 300) -> dict:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    # Signing in is required and the app's whole point is following and reacting, so
    # it asks for those once, on Civitai's consent screen, rather than a second
    # in-app switch the user has to find.
    requested_scope = READ_SCOPE | SOCIAL_WRITE
    active = client_id()
    if not active:
        raise OAuthSetupError("No Civitai application is set up yet. Register one on Civitai "
                              "and enter its client ID before connecting.")
    params = {"response_type": "code", "client_id": active, "redirect_uri": REDIRECT_URI, "scope": str(requested_scope),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    try:
        server = http.server.ThreadingHTTPServer(("localhost", CALLBACK_PORT), Callback)
    except OSError as error:
        # The redirect URI is registered against this exact port, so it cannot simply move.
        # Retrying is futile, and the generic failure message sends people in circles.
        raise OAuthSetupError(f"Port {CALLBACK_PORT} is already in use on this computer, so "
                              "Civitai cannot send the authorization back. Close whatever "
                              "is using it and try again.") from error
    server.result = None  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        webbrowser.open(f"{AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}", new=1)
        deadline = time.monotonic() + timeout
        while server.result is None and time.monotonic() < deadline: time.sleep(.2)  # type: ignore[attr-defined]
        result = server.result  # type: ignore[attr-defined]
    finally: server.shutdown(); server.server_close()
    if not result: raise RuntimeError("Civitai authorization timed out")
    if result.get("state") != state: raise RuntimeError("OAuth state validation failed")
    if result.get("error"): raise RuntimeError(f"Authorization failed: {result['error']}")
    tokens = _post("token", {"grant_type": "authorization_code", "code": result["code"], "code_verifier": verifier,
        "client_id": active, "redirect_uri": REDIRECT_URI})
    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600)); tokens["client_id"] = active
    identity = _userinfo(tokens["access_token"]); tokens["identity"] = {"id": identity.get("id"), "username": identity.get("username")}
    _save(tokens); return tokens["identity"]


def get_access_token() -> str:
    tokens = _load()
    active = client_id()
    if not active or tokens.get("client_id") != active:
        raise RuntimeError("Stored authorization belongs to a different Civitai application")
    if int(tokens.get("expires_at", 0)) <= int(time.time()) + 120:
        tokens = {**_post("token", {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": active}), "identity": tokens.get("identity", {}), "client_id": active}
        tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600)); _save(tokens)
    return tokens["access_token"]


def status() -> dict:
    tokens = _load()
    # A stored authorization belongs to one application. If that application is no longer
    # the configured one, the token cannot be used for anything, so reporting a connection
    # would be a lie that only surfaces as failures later.
    active = client_id()
    if not active or tokens.get("client_id") != active:
        raise RuntimeError("Civitai is not connected")
    scope = tokens.get("scope") or 0
    if isinstance(scope, list): scope = scope[0] if scope else 0
    # Follows and reactions are requested at sign-in, so what Civitai granted is the
    # single source of truth. A grant that came back without write access still means
    # no writes, which keeps the app from attempting one it is not allowed to make.
    granted = (int(scope) & SOCIAL_WRITE) == SOCIAL_WRITE
    return {"connected": True, **(tokens.get("identity") or {}), "scope": scope,
            "scopeGrantsWrite": granted, "socialWrite": granted}


def _revoke(token: str, hint: str, client: str) -> bool:
    """Ask Civitai to invalidate one token. Best effort: never blocks disconnecting."""
    if not token or not client:
        return False
    body = urllib.parse.urlencode({"token": token, "token_type_hint": hint,
                                   "client_id": client}).encode()
    request = urllib.request.Request(f"{AUTH_BASE}/revoke", data=body, method="POST",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status < 300
    except Exception:  # noqa: BLE001
        return False


def disconnect() -> None:
    """Revoke the authorization at Civitai, then delete it locally.

    Deleting the local token only makes this installation forget the grant; the
    authorization stays live on the user's Civitai account until it is revoked. Disconnect
    should mean disconnected, so the revocation is attempted first — but a failure must not
    strand the user with a token they cannot remove, so the local file goes either way.
    """
    client = client_id()
    try:
        tokens = _load()
    except RuntimeError:
        tokens = {}
    if tokens.get("client_id") == client:
        # The refresh token is the durable grant; revoking it takes the access token with
        # it on a compliant server, but both are sent because that is not guaranteed.
        _revoke(tokens.get("refresh_token") or "", "refresh_token", client)
        _revoke(tokens.get("access_token") or "", "access_token", client)
    if _uses_dpapi():
        for path in {TOKEN_PATH, LEGACY_TOKEN_PATH}:
            if path.exists(): path.unlink()
    elif _uses_secret_service():
        _delete_linux()
    else:
        raise _unsupported_storage()

"""Explicit headless-container OAuth transport and encrypted credential storage."""
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs

from .atomic_file import write_bytes
from .paths import data_root

_LOCK = threading.Lock()
_PENDING = None


def enabled():
    return os.environ.get("CAD_CONTAINER") == "1"


def cipher():
    from cryptography.fernet import Fernet
    path = Path(os.environ.get("CAD_CREDENTIAL_KEY_FILE", "/run/secrets/credential_key"))
    try:
        if path.resolve().is_relative_to(data_root().resolve()):
            raise ValueError("Key must be outside the data directory")
        return Fernet(path.read_bytes().strip())
    except (OSError, ValueError):
        raise RuntimeError("Container credential key is missing or invalid; mount a Fernet key outside /data") from None


def credential_path():
    return data_root() / "oauth_tokens.fernet"


def load():
    decryptor = cipher()
    path = credential_path()
    if not path.exists():
        raise RuntimeError("Civitai is not connected")
    try:
        return json.loads(decryptor.decrypt(path.read_bytes()))
    except Exception:
        raise RuntimeError("Container credentials cannot be decrypted; restore the matching key or sign out and reconnect") from None


def save(tokens):
    write_bytes(credential_path(), cipher().encrypt(json.dumps(tokens).encode()))


def authorization_url():
    with _LOCK:
        return _PENDING["url"] if _PENDING and time.monotonic() < _PENDING["deadline"] and not _PENDING["event"].is_set() else None


def authorize(url, state, timeout):
    global _PENDING
    pending = {"url": url, "state": state, "event": threading.Event(),
               "deadline": time.monotonic() + timeout, "result": None}
    with _LOCK:
        if _PENDING is not None:
            raise RuntimeError("Authorization is already pending")
        _PENDING = pending
    try:
        if not pending["event"].wait(timeout):
            raise RuntimeError("Civitai authorization timed out")
        return pending["result"]
    finally:
        with _LOCK:
            _PENDING = None


def callback(query):
    values = parse_qs(query, keep_blank_values=True)
    if any(len(value) != 1 for value in values.values()):
        return 400
    result = {key: value[0] for key, value in values.items()}
    with _LOCK:
        if not _PENDING or time.monotonic() >= _PENDING["deadline"]:
            return 400
        if not secrets.compare_digest(result.get("state", "").encode(), _PENDING["state"].encode()):
            return 400
        if not (bool(result.get("code")) ^ bool(result.get("error"))):
            return 400
        if _PENDING["event"].is_set():
            return 409
        _PENDING["result"] = result
        _PENDING["event"].set()
        return 200

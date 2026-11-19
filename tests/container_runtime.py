"""Offline checks for container credentials and single-port callback state."""
import test_safety
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from discovery import container_runtime as runtime

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    os.environ['CAD_CONTAINER'] = '1'
    os.environ['CIVITAI_HISTORY_DATA_DIR'] = str(root / 'data')
    os.environ['CAD_CREDENTIAL_KEY_FILE'] = str(root / 'key')
    key = root / 'key'
    key.write_bytes(Fernet.generate_key())
    from discovery import oauth
    payload = {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh'}
    oauth._save(payload)
    assert oauth._load() == payload
    encrypted = runtime.credential_path().read_bytes()
    assert b'synthetic' not in encrypted
    assert runtime.credential_path().stat().st_mode & 0o077 == 0
    saved_key = key.read_bytes()
    key.write_bytes(Fernet.generate_key())
    try: oauth._load(); raise AssertionError('Wrong key accepted')
    except oauth.OAuthSetupError: pass
    key.write_bytes(saved_key)
    assert oauth._load() == payload
    key.unlink()
    try: runtime.cipher(); raise AssertionError('Missing key accepted')
    except RuntimeError: pass
    key.write_bytes(saved_key)
    os.environ['CAD_CREDENTIAL_KEY_FILE'] = str(root / 'data' / 'key')
    try: runtime.cipher(); raise AssertionError('Key inside data accepted')
    except RuntimeError: pass
    os.environ['CAD_CREDENTIAL_KEY_FILE'] = str(key)
    results = []
    thread = threading.Thread(target=lambda: results.append(runtime.authorize('https://example.invalid', 'expected', 2)))
    thread.start()
    deadline = time.monotonic() + 1
    while not runtime.authorization_url() and time.monotonic() < deadline: time.sleep(.01)
    assert runtime.callback('state=wrong&code=code') == 400
    assert runtime.callback('state=expected&state=expected&code=code') == 400
    assert runtime.callback('state=expected&code=code&error=error') == 400
    assert runtime.callback('state=expected&code=code') == 200
    assert runtime.authorization_url() is None
    thread.join()
    assert results == [{'state': 'expected', 'code': 'code'}]
    assert runtime.callback('state=expected&code=code') == 400
    try: runtime.authorize('https://example.invalid', 'timeout', .01); raise AssertionError('Missing timeout')
    except RuntimeError: pass
    assert runtime.authorization_url() is None
    # Sign-out must remove the container backend even without a usable remote session.
    oauth._revoke = lambda *args: None
    oauth.disconnect()
    assert not runtime.credential_path().exists()
print('PASS: encrypted storage, wrong/missing keys, key separation, callback state, timeout, sign-out')

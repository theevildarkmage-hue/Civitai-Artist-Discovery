"""OAuth credentials use protected platform storage and never a Linux plaintext file."""

import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PasswordDeleteError(Exception):
    pass


SecretServiceBackend = type("Keyring", (), {})
SecretServiceBackend.__module__ = "keyring.backends.SecretService"
PlaintextBackend = type("PlaintextKeyring", (), {})
PlaintextBackend.__module__ = "keyrings.alt.file"


class FakeKeyring:
    def __init__(self, backend=None):
        self.backend = backend or SecretServiceBackend()
        self.values = {}
        self.errors = SimpleNamespace(PasswordDeleteError=PasswordDeleteError)

    def get_keyring(self):
        return self.backend

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        try:
            del self.values[(service, account)]
        except KeyError as error:
            raise PasswordDeleteError() from error


original_override = os.environ.get("CIVITAI_HISTORY_DATA_DIR")
original_keyring = sys.modules.get("keyring")
try:
    with tempfile.TemporaryDirectory(prefix="civitai-oauth-storage-") as temporary:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
        import discovery.oauth as oauth
        oauth = importlib.reload(oauth)
        fake = FakeKeyring()
        sys.modules["keyring"] = fake
        oauth._uses_dpapi = lambda: False
        oauth._uses_secret_service = lambda: True

        payload = {"access_token": "access", "refresh_token": "refresh",
                   "expires_at": 2 ** 31, "client_id": oauth.client_id()}
        oauth._save(payload)
        assert oauth._load() == payload
        assert not oauth.TOKEN_PATH.exists(), "Linux OAuth credentials were written to a file"
        stored = fake.values[(oauth.KEYRING_SERVICE, oauth.KEYRING_ACCOUNT)]
        assert json.loads(stored) == payload

        oauth._delete_linux()
        assert not fake.values
        oauth._delete_linux()  # Removing an already-absent credential is harmless.

        fake.backend = PlaintextBackend()
        try:
            oauth._save(payload)
            raise AssertionError("an insecure keyring backend was accepted")
        except oauth.OAuthSetupError as error:
            assert "Secret Service" in str(error), error
finally:
    if original_keyring is not None:
        sys.modules["keyring"] = original_keyring
    else:
        sys.modules.pop("keyring", None)
    if original_override is not None:
        os.environ["CIVITAI_HISTORY_DATA_DIR"] = original_override
    else:
        os.environ.pop("CIVITAI_HISTORY_DATA_DIR", None)

print({"linuxSecretServiceRoundTrip": True, "noPlaintextToken": True,
       "missingDeleteIsHarmless": True, "insecureBackendRejected": True})

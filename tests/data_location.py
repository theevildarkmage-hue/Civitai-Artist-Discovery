"""3.0 never moves existing data or credentials; explicit test roots still work."""

import test_safety  # Fail closed before any test network request.
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import paths

with tempfile.TemporaryDirectory() as base:
    base=Path(base); app=base/'new-clone'; app.mkdir()
    old=base/'CivitaiArtistDiscovery'; old.mkdir()
    marker=old/'oauth_tokens.dpapi'; marker.write_bytes(b'synthetic-do-not-migrate')
    with patch.dict(os.environ,{'LOCALAPPDATA':str(base)}), patch.object(paths,'application_root',return_value=app):
        with patch.dict(os.environ):
            os.environ.pop('CIVITAI_HISTORY_DATA_DIR',None)
            assert paths.data_root()==app/'data-dev-3.0'
            assert not paths.data_root().exists()
            assert marker.read_bytes()==b'synthetic-do-not-migrate'
        with patch.dict(os.environ,{'CIVITAI_HISTORY_DATA_DIR':str(base/'test-data')}):
            assert paths.data_root()==base/'test-data'
            assert marker.exists()
print('PASS: isolated 3.0 default, no data or credential migration, explicit override')

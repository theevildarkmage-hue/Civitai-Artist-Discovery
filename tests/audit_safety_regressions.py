"""Maintenance audit: preference transactions, credential redirects, updater paths."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery import diagnostics, oauth, social, updater
from discovery.history import HistoryArchive
from discovery.settings import AppSettings

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    message = ('Authorization: Bearer fake-secret-one\n'
               '{"authorization":"Bearer fake-secret-two", "access_token":"fake-secret-three"}\n'
               'refresh_token=fake-secret-four https://user:fake-secret-five@example.invalid/?token=fake-secret-six')
    diagnostics.append_log(root, message)
    logged = (root / 'error.log').read_text()
    assert 'fake-secret-' not in logged and '[redacted]' in logged
    archive = HistoryArchive(root / 'history')
    archive._record_api_failure({}, 'http', status=503, body=message.encode())
    assert 'fake-secret-' not in archive.api_failure_log.read_text()

with tempfile.TemporaryDirectory() as temporary:
    settings = AppSettings(Path(temporary) / 'settings.json')
    settings.update(dim_seen_cards_value=True)
    original = settings.load
    read, release = threading.Event(), threading.Event()
    errors = []

    def load():
        value = original()
        if threading.current_thread().name == 'first-update':
            read.set()
            assert release.wait(3)
        return value

    def change(**values):
        try:
            settings.update(**values)
        except Exception as error:
            errors.append(error)

    with patch.object(settings, 'load', load):
        first = threading.Thread(target=change, name='first-update', kwargs={'media_type_value': 'image'})
        second = threading.Thread(target=change, kwargs={'dim_seen_cards_value': False})
        first.start(); assert read.wait(3)
        second.start(); second.join(.1)
        release.set(); first.join(3); second.join(3)
    assert not errors and not first.is_alive() and not second.is_alive(), errors
    assert settings.load()['mediaType'] == 'image'
    assert settings.load()['dimSeenCards'] is False

received = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        received.append((self.path, self.headers.get('Authorization')))
        if self.path == '/userinfo':
            self.send_response(302)
            self.send_header('Location', f'http://localhost:{self.server.server_port}/redirected')
            self.end_headers()
        else:
            body = b'{"id":1}'
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
try:
    with patch.object(oauth, 'AUTH_BASE', base):
        assert oauth._userinfo('fixture-not-a-real-credential') == {'id': 1}
    with patch.object(social, 'get_access_token', return_value='fixture-not-a-real-credential'):
        assert social.SocialClient()._request(urllib.request.Request(base + '/userinfo')) == {'id': 1}
    assert all(value is None for path, value in received if path == '/redirected'), received
    assert all(value == 'Bearer fixture-not-a-real-credential' for path, value in received if path == '/userinfo')
finally:
    server.shutdown(); server.server_close()

with tempfile.TemporaryDirectory() as temporary:
    install = Path(temporary) / 'app'; data = install / 'data'
    stage = data / 'update' / 'staged' / 'new' / 'app'
    stage.mkdir(parents=True)
    (stage / 'app.exe').write_bytes(b'new')
    saved = data / 'personal.sqlite3'; saved.write_bytes(b'preserve')
    config = data / 'update' / 'apply.json'
    values = {'installRoot': str(install), 'stagedRoot': str(stage), 'dataRoot': str(data),
              'executableName': 'app.exe', 'backupRoot': str(data / 'update' / 'backup' / 'old'),
              'resultPath': str(data / 'update' / 'result.json')}
    for changes in [{'backupRoot': str(data)}, {'backupRoot': str(install.parent)},
                    {'resultPath': str(saved)}, {'executableName': '../app.exe'}]:
        config.write_text(json.dumps({**values, **changes}))
        try:
            updater.apply_staged_update(config, launch=False, wait_for_process=lambda _pid: None)
            raise AssertionError('Unsafe update configuration accepted')
        except RuntimeError:
            pass
        assert saved.read_bytes() == b'preserve'
        assert (stage / 'app.exe').read_bytes() == b'new'

print({'atomicPreferences': True, 'noRedirectedCredentials': True, 'updaterPathsPreserveData': True})

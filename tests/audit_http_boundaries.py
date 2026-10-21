"""Actual isolated server: Host/Origin, MIME, body limits and static confinement."""
import test_safety  # Refuse live application endpoints and isolate all test data.

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='cad3-audit-http-', ignore_cleanup_errors=True) as temporary:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(port), '--no-browser'],
        cwd=ROOT, env={**os.environ, 'CIVITAI_HISTORY_DATA_DIR': temporary},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    try:
        for _ in range(100):
            try:
                if request('GET', '/api/history/config')[0] == 200:
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Server not ready')
        assert request('GET', '/api/settings', headers={'Host': 'attacker.invalid'})[0] == 403
        for path in ['/../server.py', '/%2e%2e/data/settings.json', '/%2e%2e%5cserver.py']:
            assert request('GET', path)[0] in (403, 404), path
        for origin in ['http://attacker.invalid', 'null', 'http://127.0.0.1:1',
                       f'http://localhost:{port}', f'http://127.0.0.1:{port}/wrong']:
            assert request('POST', '/api/settings', '{}', {'Origin': origin, 'Content-Type': 'application/json'})[0] == 403, origin
        for mime in ['text/plain', 'application/json-evil']:
            assert request('POST', '/api/settings', '{}', {'Content-Type': mime})[0] == 400
        assert request('POST', '/api/settings', '{}', {'Content-Type': 'application/json', 'Content-Length': '8193'})[0] == 400
        assert request('POST', '/api/settings', '{bad', {'Content-Type': 'application/json'})[0] == 400
        status, headers, body = request('POST', '/api/settings', json.dumps({'dimSeenCards': False}),
            {'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json; charset=utf-8'})
        assert status == 200 and json.loads(body)['dimSeenCards'] is False
        assert not any(name.lower() == 'access-control-allow-origin' for name, _ in headers)
        static = dict(request('GET', '/')[1])
        assert "script-src 'self'" in static['Content-Security-Policy']
        assert "frame-ancestors 'none'" in static['Content-Security-Policy']
        assert static['X-Content-Type-Options'] == 'nosniff'
    finally:
        process.terminate(); process.wait(timeout=10)
print({'hostOriginMimeLimits': True, 'staticConfinement': True, 'cspAndCors': True})

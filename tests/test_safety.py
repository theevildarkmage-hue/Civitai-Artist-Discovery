"""Fail-closed transport guard shared by script-based tests.

Only loopback endpoints launched by this test process (with isolated data), or
explicitly challenged test instances, may receive actual network traffic.
Mocked Playwright routes may still fulfill requests without touching the network.
"""
from __future__ import annotations
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import atexit
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPORTS = Path(os.environ.get('CAD_TEST_REPORT_DIR') or
               tempfile.mkdtemp(prefix='cad3-test-artifacts-')).resolve()
REPORTS.mkdir(parents=True,exist_ok=True)
FORBIDDEN_PORTS = {8906, 33101, 33201}
ENDPOINTS = {}
OWNED_PROCESSES = []
_installed = False
_default_data = None


def address(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or not parsed.port or parsed.port in FORBIDDEN_PORTS):
        raise PermissionError('Test network target is not an isolated loopback endpoint')
    return parsed.port


def check(url):
    port = address(url)
    owner = ENDPOINTS.get(port)
    if owner is not None and ((hasattr(owner,'poll') and owner.poll() is not None)
            or (hasattr(owner,'socket') and owner.socket.fileno()<0)):
        ENDPOINTS.pop(port,None);owner=None
    if owner is None:
        for process, root in OWNED_PROCESSES:
            if process.poll() is not None: continue
            try:
                instance = json.loads((root / 'running-instance.json').read_text(encoding='utf-8'))
                if instance.get('pid') == process.pid and address(instance['url']) == port:
                    ENDPOINTS[port] = owner = process; break
            except (OSError, ValueError, KeyError, PermissionError):
                continue
    if (owner is None or (hasattr(owner, 'poll') and owner.poll() is not None)
            or (hasattr(owner, 'socket') and owner.socket.fileno() < 0)):
        raise PermissionError('Test endpoint was not explicitly registered or its process exited')
    return port


def isolated_root(value):
    if not value:
        raise PermissionError('Test server requires an explicit isolated data directory')
    path = Path(value).resolve()
    if path in (ROOT/'data-test-community',ROOT/'data-dev-3.0'):
        raise PermissionError('Existing interactive 3.0 profiles are protected from automated tests')
    pin=ROOT/'.data-directory-3.0.json'
    if pin.exists() and path==(ROOT/json.loads(pin.read_text(encoding='utf-8'))['directory']).resolve():
        raise PermissionError('The selected interactive 3.0 profile is not a test sandbox')
    # Never accept an existing installation, a source archive, or an ancestor.
    protected = ROOT.parent / 'Civitai Desktop Client'
    if path == ROOT or path in ROOT.parents or path == protected or protected in path.parents:
        raise PermissionError('Test data directory overlaps a protected installation')
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not (path.name.startswith(('cad3-', 'civitai-', 'data-test-'))
            and (ROOT in path.parents or temp_root in path.parents)):
        raise PermissionError('Test data directory is not an explicit test workspace')
    return path


def install():
    global _installed, _default_data
    if _installed:
        return
    _installed = True
    existing_root = os.environ.get('CIVITAI_HISTORY_DATA_DIR')
    if existing_root:
        isolated_root(existing_root)
    else:
        _default_data = tempfile.TemporaryDirectory(prefix='cad3-test-default-', ignore_cleanup_errors=True)
        os.environ['CIVITAI_HISTORY_DATA_DIR'] = _default_data.name
    original_popen = subprocess.Popen
    class GuardedPopen(original_popen):
        def __init__(self, args, *positional, **kwargs):
            values = [str(x) for x in args] if isinstance(args, (list, tuple)) else []
            port = None
            root = None
            if any(Path(x).name == 'server.py' for x in values):
                entry = next(x for x in values if Path(x).name == 'server.py')
                entry_path = Path(entry)
                if not entry_path.is_absolute(): entry_path = Path(kwargs.get('cwd') or Path.cwd()) / entry_path
                if entry_path.resolve() != ROOT / 'server.py':
                    raise PermissionError('Tests may launch only this isolated 3.0 server source')
                env = kwargs.get('env') or os.environ
                root = isolated_root(env.get('CIVITAI_HISTORY_DATA_DIR'))
                if '--port' in values:
                    port = int(values[values.index('--port') + 1])
                    if port in FORBIDDEN_PORTS:
                        raise PermissionError('Test server may not target the daily-use or retired ports')
                    with socket.socket() as probe:
                        try:probe.bind(('127.0.0.1',port))
                        except OSError:raise PermissionError('Requested test port is occupied; refusing fallback to a live application') from None
            super().__init__(args, *positional, **kwargs)
            if root: OWNED_PROCESSES.append((self, root))
            # Register only after running-instance.json proves the actual bound
            # port and PID. Requested arguments are not proof of ownership.
    subprocess.Popen = GuardedPopen
    original_request = http.client.HTTPConnection.putrequest
    def guarded_request(connection, method, url, *args, **kwargs):
        check(f'http://{connection.host}:{connection.port}')
        return original_request(connection, method, url, *args, **kwargs)
    http.client.HTTPConnection.putrequest = guarded_request
    # In-process static/fixture servers are explicitly owned by this test process.
    original_init = http.server.HTTPServer.__init__
    def server_init(server, server_address, *args, **kwargs):
        if server_address[1] in FORBIDDEN_PORTS:
            raise PermissionError('A test attempted to bind a protected port')
        original_init(server, server_address, *args, **kwargs)
        if server.server_address[0] in ('127.0.0.1', '::1'):
            ENDPOINTS[server.server_address[1]] = server
    http.server.HTTPServer.__init__ = server_init
    try:
        from playwright.sync_api import Browser, BrowserContext
    except ImportError:
        return
    def route_guard(route):
        try:
            check(route.request.url)
        except (PermissionError, ValueError):
            route.abort('blockedbyclient')
        else:
            route.continue_()
    original_page = Browser.new_page
    def browser_page(browser, *args, **kwargs):
        page = original_page(browser, *args, **kwargs)
        page.route('**/*', route_guard)
        return page
    Browser.new_page = browser_page
    original_context = Browser.new_context
    def browser_context(browser, *args, **kwargs):
        context = original_context(browser, *args, **kwargs)
        context.route('**/*', route_guard)
        return context
    Browser.new_context = browser_context
    original_context_page = BrowserContext.new_page
    def context_page(context, *args, **kwargs):
        page = original_context_page(context, *args, **kwargs)
        page.route('**/*', route_guard)
        return page
    BrowserContext.new_page = context_page


class IsolatedEndpoint:
    """Challenge an explicitly configured instance before any mutation is permitted."""
    def __init__(self, base, instance_id, data_root):
        self.base, self.instance_id = base.rstrip('/'), instance_id
        self.port = address(self.base)
        if not instance_id or len(instance_id) < 24:
            raise PermissionError('An isolated instance challenge is required')
        self.root = isolated_root(data_root)
        self.identity = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()
        self.verified = False

    def verify(self):
        # The endpoint must belong to a process explicitly launched by this test.
        check(self.base)
        previous = ENDPOINTS.get(self.port)
        if previous is None or (hasattr(previous, 'poll') and previous.poll() is not None):
            raise PermissionError('Test endpoint process is not registered or has exited')
        try:
            with urllib.request.urlopen(self.base + '/api/test-isolation', timeout=3) as response:
                value = json.load(response)
            if value != {'instance': self.instance_id, 'dataIdentity': self.identity, 'fixture': True}:
                raise PermissionError('Isolated endpoint challenge failed')
        except Exception:
            ENDPOINTS[self.port] = previous
            raise
        self.verified = True
        return self

    def request(self, path, body=None, *, member=None, timeout=10):
        if not self.verified or not path.startswith('/') or path.startswith('//'):
            raise PermissionError('Mutation target has not passed the isolation challenge')
        check(self.base)
        headers = {'Content-Type': 'application/json', 'X-CAD-Test-Instance': self.instance_id}
        if member is not None: headers['Cookie'] = f'cad_fixture_member={member}'
        request = urllib.request.Request(self.base + path, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)


def _cleanup_owned_processes():
    for process, _ in OWNED_PROCESSES:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)


atexit.register(_cleanup_owned_processes)
install()

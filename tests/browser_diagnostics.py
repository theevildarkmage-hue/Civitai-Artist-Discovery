"""Browser removals and artwork failures reach the local log without URL secrets."""
import test_safety  # Refuse live application endpoints and isolate all test data.

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, install_fixture

sys.path.insert(0, str(ROOT))
from discovery import diagnostics


with tempfile.TemporaryDirectory(prefix='cad3-browser-diagnostics-', ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(port), '--no-browser'],
        cwd=ROOT, env={**os.environ, 'CIVITAI_HISTORY_DATA_DIR': temporary},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/api/history/config', timeout=1).close()
                break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('server did not start')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            install_fixture(page, base)
            page.route('**/api/diagnostics/browser', lambda route: route.continue_())

            def tags(route):
                ids = json.loads(route.request.post_data)['imageIds']
                route.fulfill(content_type='application/json', body=json.dumps({'images': {
                    str(i): {'known': True, 'tags': [{'name': 'hidden', 'hidden': True}] if i == 1 else []}
                    for i in ids}}))
            page.route('**/api/history/tags', tags)

            def artwork(route):
                if urlsplit(route.request.url).path.endswith('recover-original.svg'):
                    route.fulfill(content_type='image/svg+xml', body=
                        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>')
                else:
                    route.fulfill(status=404, body='Missing')
            page.route('https://image.civitai.com/log-test/**', artwork)
            page.goto(base, wait_until='domcontentloaded')
            page.locator('.creator-card').first.wait_for(timeout=10000)
            page.evaluate('''async () => {
                const {showArtwork} = await import('/ui/artwork.js');
                for (const [id, original] of [[101,'recover-original.svg'],[102,'missing-original.svg']]) {
                    const card=document.createElement('article'); card.className='creator-card';
                    card.dataset.id=id; card.dataset.username='DiagnosticFixture';
                    const image=new Image(); image.loading='eager'; card.append(image); document.body.prepend(card);
                    showArtwork(image, 'https://image.civitai.com/log-test/missing-preview.svg?token=fake-secret',
                        `https://image.civitai.com/log-test/${original}?token=fake-secret`);
                }
            }''')
            logfile = root / 'error.log'
            for _ in range(80):
                page.wait_for_timeout(100)
                logged = logfile.read_text(encoding='utf-8') if logfile.exists() else ''
                if all(value in logged for value in ('card_removed', 'image_load_recovered', 'original-failed')):
                    break
            else:
                raise AssertionError(logged)
            assert 'all-images-hidden-by-civitai-tags' in logged
            assert '"imageId": 101' in logged and '"imageId": 102' in logged
            assert 'fake-secret' not in logged and '?token=' not in logged
            browser.close()

        bad = urllib.request.Request(base + '/api/diagnostics/browser', data=b'{"events":[{"event":"unknown"}]}',
                                     headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(bad)
            raise AssertionError('unknown event accepted')
        except urllib.error.HTTPError as error:
            assert error.code == 400
    finally:
        process.terminate()
        process.wait(timeout=15)

    # Rotation preserves the previous log, and only allowlisted event fields reach disk.
    diagnostics.LOG_BYTES = 100
    diagnostics.log_browser_events(root, [{'event': 'image_load_failed', 'imageId': 99,
        'url': 'https://image.civitai.com/test.svg?token=fake-secret#fragment',
        'authorization': 'fake-secret'}])
    assert (root / 'error.log.1').exists()
    assert 'fake-secret' not in (root / 'error.log').read_text()

print({'cardRemovalOnDisk': True, 'previewFailureAndRecovery': True,
       'originalFailureOnDisk': True, 'urlSecretsExcluded': True, 'logRotates': True})

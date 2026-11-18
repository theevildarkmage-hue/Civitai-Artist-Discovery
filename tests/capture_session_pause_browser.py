"""Compact collection controls show their state and persist without explanatory clutter."""

import test_safety  # Fail closed before any test network request.

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8899
SHOTS = test_safety.REPORTS / "auto-capture"
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="civitai-capture-ui-") as temporary:
    import json
    sys.path.insert(0,str(ROOT))
    from discovery.settings import AppSettings
    settings=AppSettings(Path(temporary)/'settings.json');settings.update(auto_capture_value=True)
    saved=settings.path.read_bytes()
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser", "--pause-auto-capture"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1)
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.1)

        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1280,'height':900})
            page.route('**/api/auth-status',lambda r:r.fulfill(content_type='application/json',body=json.dumps({'connected':True,'socialWrite':False,'id':7,'username':'Synthetic'})))
            page.route('**/api/discovery/summary',lambda r:r.fulfill(content_type='application/json',body='{"hasData":true}'))
            page.goto(f'http://127.0.0.1:{PORT}',wait_until='domcontentloaded')
            page.locator('#galleryPreferences').click()
            page.locator('#captureNextRun',has_text='Paused for this launch').wait_for()
            assert page.locator('#captureEnabled').is_checked()
            assert page.locator('#captureRunNow').is_disabled()
            assert settings.path.read_bytes()==saved
            status=page.evaluate("async()=>await(await fetch('/api/history/capture')).json()")
            assert status['enabled'] and status['sessionPaused'] and not status['running'] and status['nextRunAt'] is None
            page.locator('#captureInterval').select_option('24')
            page.wait_for_function("()=>!document.querySelector('#captureInterval').disabled")
            assert page.locator('#captureRunNow').is_disabled()
            assert json.loads(settings.path.read_text())['autoCapture'] is True
            page.reload(wait_until='domcontentloaded');page.locator('#galleryPreferences').click()
            page.locator('#captureNextRun',has_text='Paused for this launch').wait_for()
            assert page.locator('#captureRunNow').is_disabled()
            page.screenshot(path=str(test_safety.REPORTS/'capture-session-pause.png'))
            browser.close()
    finally:
        process.terminate();process.wait(timeout=20)
print('PASS: real --pause-auto-capture startup preserves enabled preference; API/UI show paused; run-now remains disabled after settings save/reload; isolated unsigned profile')

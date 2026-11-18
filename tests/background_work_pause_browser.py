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
                                "--no-browser", "--pause-background-work"], cwd=ROOT, env=environment)
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

        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/discovery/summary',timeout=10) as response: actual_summary=json.load(response)
        assert actual_summary.get('automaticRefreshPaused') is True
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1280,'height':900})
            page.route('**/api/auth-status',lambda r:r.fulfill(content_type='application/json',body=json.dumps({'connected':True,'socialWrite':False,'id':7,'username':'Synthetic'})))
            page.route('**/api/discovery/summary',lambda r:r.fulfill(content_type='application/json',body=json.dumps(actual_summary)))
            page.clock.install()
            page.goto(f'http://127.0.0.1:{PORT}',wait_until='domcontentloaded')
            page.locator('#galleryPreferences').click()
            page.locator('#captureNextRun',has_text='Paused for this launch').wait_for()
            assert page.locator('#captureEnabled').is_checked()
            assert page.locator('#captureRunNow').is_disabled()
            assert settings.path.read_bytes()==saved
            status=page.evaluate("async()=>await(await fetch('/api/history/capture')).json()")
            assert status['enabled'] and status['sessionPaused'] and not status['running'] and status['nextRunAt'] is None
            summary=page.evaluate("async()=>await(await fetch('/api/discovery/summary')).json()")
            assert summary.get('automaticRefreshPaused') is True
            calls=[]
            page.route('**/api/discovery/status',lambda r:r.fulfill(content_type='application/json',body='{"running":false}'))
            page.route('**/api/discovery/sync',lambda r:(calls.append(r.request.post_data_json),r.fulfill(content_type='application/json',body='{"running":false,"phase":"complete","message":"Synthetic manual completion"}'))[-1])
            page.evaluate('''summary=>{
              scheduleAutomaticProfileRefresh({hasData:true,lastSyncAt:null},10000);
              scheduleAutomaticProfileRefresh({...summary,lastSyncAt:null},0);
            }''',summary)
            page.clock.fast_forward(60000);page.wait_for_timeout(50)
            assert not calls,'Paused startup must not start automatic profile work'
            page.keyboard.press('Escape');page.locator('#tabDiscovery').click()
            page.locator('#syncDiscovery').click()
            page.wait_for_function('()=>!document.querySelector("#syncDiscovery").disabled')
            assert len(calls)==1,'Explicit manual analysis remains available'
            page.locator('#galleryPreferences').click()
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
print('PASS: real background-pause CLI/API; saved settings retained; automatic refresh suppressed; explicit manual action allowed through synthetic transport; restart behavior covered separately')

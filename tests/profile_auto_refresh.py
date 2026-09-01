"""A stale profile refreshes quietly, while a recent one does not."""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PORT = 8894


with tempfile.TemporaryDirectory(prefix="civitai-profile-auto-refresh-", ignore_cleanup_errors=True) as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1)
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            calls = {"sync": 0}
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"connected":false}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))
            page.route("**/api/discovery/status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"running":false,"phase":"complete","message":"Ready"}'))

            def sync_route(route):
                calls["sync"] += 1
                route.fulfill(status=202, content_type="application/json",
                    body='{"running":false,"phase":"complete","message":"Ready"}')

            page.route("**/api/discovery/sync", sync_route)
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#welcome:not(.hidden)", timeout=20000)

            due = page.evaluate("""() => ({
                missing: profileRefreshDue({hasData: true, lastSyncAt: null}),
                stale: profileRefreshDue({hasData: true, lastSyncAt: new Date(Date.now() - 25*60*60*1000).toISOString()}),
                recent: profileRefreshDue({hasData: true, lastSyncAt: new Date(Date.now() - 23*60*60*1000).toISOString()}),
                empty: profileRefreshDue({hasData: false, lastSyncAt: null})
            })""")
            assert due == {"missing": True, "stale": True, "recent": False, "empty": False}, due

            page.evaluate("""() => {
                oauthConnected = true;
                dayBuilt = true;
                activeBuildSegment = null;
                activeRebuild = false;
                scheduleAutomaticProfileRefresh({hasData: true,
                    lastSyncAt: new Date(Date.now() - 25*60*60*1000).toISOString()}, 0);
            }""")
            page.wait_for_function("() => recommendationsNeedRefresh")
            assert calls["sync"] == 1, calls

            page.evaluate("""() => scheduleAutomaticProfileRefresh({hasData: true,
                lastSyncAt: new Date().toISOString()}, 0)""")
            page.wait_for_timeout(100)
            assert calls["sync"] == 1, calls
            browser.close()
    finally:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close",
                data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=2)
        except Exception:
            process.terminate()
        process.wait(timeout=10)

print({"twentyFourHourCheck": True, "staleRefreshStarts": True,
       "recentProfileSkipped": True, "emptyProfileLeftForOnboarding": True})

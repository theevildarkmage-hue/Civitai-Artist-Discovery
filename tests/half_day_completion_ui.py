"""A completed half stays viewable and offers an explicit path to finish the day."""

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
PORT = 8891


with tempfile.TemporaryDirectory(prefix="civitai-half-completion-", ignore_cleanup_errors=True) as temporary:
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
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/history/blocks**", lambda route: route.fulfill(status=200,
                content_type="application/json", body=json.dumps({"blocks": {
                    "morning": {"complete": True, "itemCount": 12000, "state": "complete"},
                    "evening": {"complete": False, "itemCount": 0, "state": "not_started"},
                    "all": {"complete": False, "itemCount": 0, "state": "not_started"}}})))

            def status(route):
                segment = route.request.url.split("segment=")[-1].split("&")[0]
                complete = segment == "morning"
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "state": "complete" if complete else "not_started", "phase": "complete" if complete else "waiting",
                    "complete": complete, "archiveComplete": complete, "itemCount": 12000 if complete else 0,
                    "creatorCount": 900 if complete else 0, "progress": 100 if complete else 0,
                    "metrics": {"elapsedSeconds": 420}}))
            page.route("**/api/history/status**", status)
            page.route("**/api/history/day**", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"complete":true,"imageCount":12000,"artistCount":900}'))
            page.route("**/api/history/artists**", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"artists":[],"total":0,"hasMore":false}'))
            page.route("**/api/history/estimate**", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"lowSeconds":300,"highSeconds":420,"measured":true}'))

            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#completeDayPrompt:not(.hidden)")
            assert "Morning is ready" in page.locator("#completeDayPrompt").inner_text()
            assert page.locator("#daySegment").input_value() == "morning"

            page.locator("#completeDay").click()
            page.wait_for_selector("#buildSetup:not(.hidden)")
            full = page.locator('#buildRange [data-segment="all"]')
            assert "Build Evening only" in full.inner_text()
            assert full.get_attribute("class") == "selected"
            assert page.locator("#startLoading").inner_text() == "Build missing half"
            assert page.locator(".segment-toolbar").is_hidden()
            browser.close()
    finally:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close",
                data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=2)
        except Exception:
            process.terminate()
        process.wait(timeout=10)

print({"completedHalfRemainsViewable": True, "missingHalfNamed": True,
       "fullDayBuildSkipsReadyHalf": True})

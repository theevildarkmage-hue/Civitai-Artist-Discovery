"""Browser workflow for update notification, changelog, progress, and install handoff."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8896

with tempfile.TemporaryDirectory(prefix="civitai-update-ui-") as temporary:
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
                time.sleep(.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            phase = {"value": "available", "checks": 0, "installs": 0, "supported": True}
            release = {"version": "0.4.0-beta.2", "name": "Civitai Artist Discovery 0.4 Beta 2",
                       "notes": "A safer updater.\n\n<img src=x onerror=alert(1)>",
                       "assetSize": 31_457_280, "prerelease": True,
                       "pageUrl": "https://github.com/theevildarkmage-hue/Civitai-Artist-Discovery/releases/tag/v0.4.0-beta.2"}

            def state():
                job = {"phase": phase["value"], "downloaded": 31_457_280 if phase["value"] == "ready" else 0,
                       "total": 31_457_280, "error": None}
                return {"enabled": True, "supported": phase["supported"], "currentVersion": "0.3.1-beta.1",
                        "available": True, "release": release, "job": job,
                        "lastResult": None, "busyReason": None}

            def updates(route):
                path = route.request.url.split("/api/update/", 1)[1]
                if path == "download":
                    phase["value"] = "downloading"
                    route.fulfill(status=202, content_type="application/json", body=json.dumps(state()))
                    phase["value"] = "ready"
                elif path == "install":
                    phase["installs"] += 1
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"installing":true,"version":"0.4.0-beta.2"}')
                elif path == "check":
                    phase["checks"] += 1
                    route.fulfill(status=202, content_type="application/json", body=json.dumps(state()))
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(state()))

            page.route("**/api/update/**", updates)
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="networkidle")
            assert page.locator("#appVersion").inner_text() == "v0.3.1-beta.1"
            page.locator("#updateAvailable").wait_for(state="visible")
            assert page.locator("#updateAvailable").inner_text() == "Update 0.4.0-beta.2"
            page.locator("#updateAvailable").click()
            page.locator("#updateDialog").wait_for(state="visible")
            assert page.locator("#updateTitle").inner_text() == release["name"]
            assert "30.0 MB" in page.locator("#updateSummary").inner_text()
            assert page.locator("#updateNotes").inner_text() == release["notes"]
            assert page.locator("#updateNotes img").count() == 0
            page.locator("#runUpdate").click()
            page.locator("#runUpdate", has_text="Install and restart").wait_for()
            assert page.locator("#updateProgress").get_attribute("value") == "100"
            page.locator("#runUpdate").click()
            page.locator("#runUpdate", has_text="Closing and installing").wait_for()
            assert phase["installs"] == 1
            page.locator("#laterUpdate").click()
            assert not page.locator("#updateDialog").is_visible()
            phase["supported"] = False
            page.reload(wait_until="networkidle")
            assert page.locator("#appVersion").inner_text() == "v0.3.1-beta.1"
            assert not page.locator("#updateAvailable").is_visible()
            assert not page.locator("#runUpdate").is_visible()
            assert "source checkout" in page.locator("#updatePreferenceStatus").inner_text().lower()
            browser.close()
        assert not errors, errors
        print({"versionAlwaysVisible": True, "sourceInstallActionHidden": True,
               "notificationVisible": True, "changelogPlainText": True,
               "downloadProgress": True, "installHandoff": True,
               "dialogDismisses": True, "scriptInjectionBlocked": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

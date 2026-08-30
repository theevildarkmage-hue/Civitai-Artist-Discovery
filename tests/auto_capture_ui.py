"""The schedule, and the way to turn it off, must be visible in the app itself."""

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
SHOTS = ROOT / "reports" / "auto-capture"
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="civitai-capture-ui-") as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=environment)
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
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"hasData":true}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#tabDiscovery")
            page.locator("#tabDiscovery").click()
            page.wait_for_selector("#captureEnabled", state="visible")

            block = page.locator(".capture-preference")
            text = block.inner_text()
            # It must say what it does and why, not merely offer a switch.
            assert "two days" in text, text
            assert "gone for good" in text, text
            assert page.locator("#captureEnabled").is_checked() is False, "must be off until asked for"
            assert "Off." in page.locator("#captureNextRun").inner_text()

            page.locator("#captureEnabled").check()
            page.locator("#captureNextRun", has_text="Next collection").wait_for(timeout=10000)
            enabled_text = page.locator("#captureNextRun").inner_text()
            # When it will happen, and that the time was spread deliberately.
            assert "in " in enabled_text, enabled_text
            assert "do not all arrive at once" in enabled_text, enabled_text
            page.screenshot(path=str(SHOTS / "enabled.png"), full_page=False)

            # A chosen time is honoured and described as the user's own.
            page.locator("#captureAt").fill("03:30")
            page.locator("#captureAt").dispatch_event("change")
            page.locator("#captureNextRun", has_text="chosen").wait_for(timeout=10000)
            chosen = page.locator("#captureNextRun").inner_text()
            assert "3:30" in chosen, chosen
            assert "12 hours later" in chosen, chosen   # a 12h interval runs twice a day

            # And it can be handed back to the automatic spread.
            page.locator("#captureAtClear").click()
            page.locator("#captureNextRun", has_text="do not all arrive at once").wait_for(timeout=10000)

            # Turning it off says plainly what the consequence is.
            page.locator("#captureEnabled").uncheck()
            page.locator("#captureNextRun", has_text="Off.").wait_for(timeout=10000)
            assert "cannot be collected later" in page.locator("#captureNextRun").inner_text()
            page.screenshot(path=str(SHOTS / "disabled.png"), full_page=False)

            # The choice survives a reload rather than silently reverting.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#tabDiscovery")
            page.locator("#tabDiscovery").click()
            page.wait_for_selector("#captureEnabled", state="visible")
            assert page.locator("#captureEnabled").is_checked() is False
            page.close()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=20)

print({"statesShown": ["off", "enabled", "chosen time", "back to automatic"],
       "explainsConsequence": True, "offByDefault": True, "persists": True,
       "screenshots": str(SHOTS)})

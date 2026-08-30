"""The Time Machine tab explains itself, and shows priming progress in creators."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8901
SHOTS = ROOT / "reports" / "time-machine"
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="civitai-tm-ui-", ignore_cleanup_errors=True) as temporary:
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

            # Three creators, one already read to the end, one with more to fetch.
            cards = [
                {"username": "Ana", "id": 1, "createdAt": "2024-09-11T00:00:00.000Z",
                 "url": "http://x/1.jpg", "thumbnailUrl": "http://x/1.jpg",
                 "civitaiUrl": "https://civitai.red/images/1", "browsingLevel": 1,
                 "stats": {}, "seenCount": 3, "knownCount": 177, "complete": True},
                {"username": "Bo", "id": 2, "createdAt": "2025-12-06T00:00:00.000Z",
                 "url": "http://x/2.jpg", "thumbnailUrl": "http://x/2.jpg",
                 "civitaiUrl": "https://civitai.red/images/2", "browsingLevel": 1,
                 "stats": {}, "seenCount": 0, "knownCount": 192, "complete": False},
            ]
            status = {"creators": 3, "primed": 2, "images": 369,
                      "priming": False, "progress": 66.7}
            page.route("**/api/timemachine", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=__import__("json").dumps({"cards": cards, "status": status})))

            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#tabTimeMachine")
            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")

            body = page.locator("#timeMachine").inner_text()
            # It must say what it is for, not just show pictures.
            assert "oldest artwork" in body, body
            assert "cannot reach this far back" in body, body

            # Progress is counted in creators, because no per-creator image total exists.
            page.locator("#timeMachineStatus", has_text="2 of 3 creators read").wait_for(timeout=10000)
            assert "369 images available" in page.locator("#timeMachineStatus").inner_text()
            width = page.locator("#timeMachineBar").evaluate("el => el.style.width")
            assert width.startswith("66"), width

            # A finished creator reads as a plain count; an unfinished one says "so far",
            # because claiming a complete denominator would be a guess.
            grid = page.locator("#timeMachineGrid").inner_text()
            assert "3 of 177" in grid and "so far" not in grid.split("3 of 177")[0][-30:], grid
            assert "0 of 192 so far" in grid, grid

            # The other tabs still work; gallery chrome is hidden here.
            assert page.locator(".segment-toolbar").is_hidden()
            page.locator("#tabDiscovery").click()
            page.wait_for_selector("#discovery:not(.hidden)")
            assert page.locator("#timeMachine").is_hidden()
            page.locator("#tabGallery").click()
            page.wait_for_selector("#timeMachine", state="hidden")

            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            clipped = page.locator(".tm-card .tm-progress").evaluate_all(
                "els => els.some(el => el.scrollWidth > el.clientWidth + 1)")
            assert not clipped, "the progress label must not be cut off by the card edge"
            page.screenshot(path=str(SHOTS / "tab.png"), full_page=False)
            page.close()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=20)

print({"tabSwitches": True, "explainsItself": True, "progressInCreators": True,
       "partialCountsMarkedSoFar": True, "screenshots": str(SHOTS)})

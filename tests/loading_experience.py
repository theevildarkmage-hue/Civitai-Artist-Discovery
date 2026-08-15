"""Deterministic usability checks for the unified history loading experience."""

from __future__ import annotations

from datetime import datetime, timedelta
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
sys.path.insert(0, str(ROOT))
from discovery.history import conservative_eta_range


assert conservative_eta_range(None) is None
assert conservative_eta_range(9) is None
assert conservative_eta_range(120) == (60, 180)
assert conservative_eta_range(600) == (420, 900)

SCREENSHOTS = ROOT / "reports" / "loading-validation"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
PORT = 8879


def response(status: dict) -> str:
    return json.dumps({"date": "2026-07-31", "state": "loading", "progress": 0,
        "pages": 0, "phase": "locating", "itemCount": 0, "creatorCount": 0,
        "elapsedSeconds": 0, "complete": False, **status})


with tempfile.TemporaryDirectory(prefix="civitai-loading-test-", ignore_cleanup_errors=True) as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT), "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, height, label in ((1440, 900, "desktop"), (900, 900, "tablet"), (430, 850, "mobile")):
                starts = {"count": 0}
                page = browser.new_page(viewport={"width": width, "height": height})
                page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                    content_type="application/json", body='{"hasData":true}'))
                page.route("**/api/auth-status", lambda route: route.fulfill(status=200, content_type="application/json", body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
                def responsive_start(route):
                    starts["count"] += 1
                    route.fulfill(status=202, content_type="application/json", body=response({"elapsedSeconds": 0}))
                page.route("**/api/history/start", responsive_start)
                def responsive_status(route):
                    value = {"elapsedSeconds": 18} if starts["count"] else {"state": "not_started", "elapsedSeconds": None}
                    route.fulfill(status=200, content_type="application/json", body=response(value))
                page.route("**/api/history/status**", responsive_status)
                page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
                page.wait_for_selector("#startLoading:not(.hidden)")
                assert starts["count"] == 0
                assert page.locator("#stopLoading").is_hidden()
                assert page.locator("#buildSetup").is_visible()
                assert page.locator(".segment-toolbar").is_hidden(), "gallery filters leaked into build setup"
                safe_estimate = page.locator("#buildEstimate").inner_text()
                page.locator('#buildCoverage [data-rating="X"]').click()
                page.wait_for_function("previous => document.getElementById('buildEstimate').textContent !== previous",
                                       arg=safe_estimate)
                assert page.locator("#buildEstimate").inner_text() != safe_estimate
                page.locator('#buildCoverage [data-rating="Soft"]').click()
                page.screenshot(path=str(SCREENSHOTS / f"ready-{label}.png"), full_page=True)
                page.locator("#startLoading").click()
                page.wait_for_selector("#phaseFinding.active")
                body = page.locator("body").inner_text()
                assert "API pages" not in body
                assert "No images are being downloaded" not in body  # replaced by the more precise preview wording
                assert "Artwork previews load later" in body
                assert "artwork listings checked" in body
                assert starts["count"] == 1
                assert page.locator("#stopLoading").is_visible() and page.locator("#closeLoading").is_visible()
                page.screenshot(path=str(SCREENSHOTS / f"finding-{label}.png"), full_page=True)
                page.close()

            states = [
                {"phase": "locating", "elapsedSeconds": 5},
                {"phase": "collecting", "progress": 10, "itemCount": 400, "creatorCount": 90, "elapsedSeconds": 12},
                {"phase": "collecting", "progress": 40, "itemCount": 2800, "creatorCount": 620, "elapsedSeconds": 24, "etaLowSeconds": 120, "etaHighSeconds": 240},
                {"phase": "locating", "progress": 0, "itemCount": 2800, "creatorCount": 620, "elapsedSeconds": 25},
                {"phase": "locating", "progress": 0, "itemCount": 2800, "creatorCount": 620, "elapsedSeconds": 26},
                {"phase": "collecting", "progress": 40, "itemCount": 2800, "creatorCount": 620, "elapsedSeconds": 30, "delayReason": "rate_limited"},
                {"phase": "organizing", "progress": 100, "itemCount": 8160, "creatorCount": 1649, "elapsedSeconds": 45},
            ]
            calls = {"status": 0, "cancelled": False, "started": False, "startRequests": 0}
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200, content_type="application/json", body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200, content_type="application/json", body='{"hasData":true}'))
            def start_route(route):
                calls["started"] = True
                calls["startRequests"] += 1
                route.fulfill(status=202, content_type="application/json", body=response(states[0]))
            page.route("**/api/history/start", start_route)

            def status_route(route):
                if not calls["started"]:
                    route.fulfill(status=200, content_type="application/json", body=response({"state": "not_started", "elapsedSeconds": None}))
                    return
                index = min(calls["status"] + 1, len(states) - 1)
                calls["status"] = index
                route.fulfill(status=200, content_type="application/json", body=response(states[index]))

            def cancel_route(route):
                calls["cancelled"] = True
                route.fulfill(status=200, content_type="application/json", body='{"cancelled":true}')

            page.route("**/api/history/status**", status_route)
            page.route("**/api/history/cancel", cancel_route)
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#startLoading:not(.hidden)")
            assert calls["startRequests"] == 0
            # Dates follow the clock: the app opens yesterday, so these must be relative.
            def long_date(days_back):
                value = datetime.now().date() - timedelta(days=days_back)
                return f"{value:%A, %B} {value.day}, {value.year}"

            page.locator("#olderDay").click()
            page.locator("#selectedDate", has_text=long_date(2)).wait_for()
            assert calls["startRequests"] == 0
            page.locator("#newerDay").click()
            page.locator("#selectedDate", has_text=long_date(1)).wait_for()
            assert calls["startRequests"] == 0
            page.locator("#startLoading").evaluate("button => { button.click(); button.click(); }")
            page.wait_for_selector("#phaseCollecting.active", timeout=10000)
            assert calls["startRequests"] == 1
            page.locator("#progressText", has_text="About 4 minutes remaining").wait_for(timeout=10000)
            widths = []
            for _ in range(2):
                widths.append(float(page.locator("#progressBar").evaluate("element => parseFloat(element.style.width)")))
                time.sleep(.2)
            assert widths == sorted(widths)
            time.sleep(1)
            assert page.locator("#phaseCollecting").get_attribute("class") == "active"
            assert "Finding" not in page.locator("#loadingMessage").inner_text()
            page.locator("#loadingMessage", has_text="asked us to slow down").wait_for(timeout=10000)
            page.wait_for_selector("#phaseOrganizing.active", timeout=10000)
            assert "8,160 images into 1,649 creator galleries" in page.locator("#loadingMessage").inner_text()
            page.locator("#stopLoading").click()
            page.locator("#loadingTitle", has_text="Loading stopped").wait_for()
            assert calls["cancelled"]
            assert "Everything collected so far has been saved" in page.locator("#loadingMessage").inner_text()
            page.screenshot(path=str(SCREENSHOTS / "stopped-desktop.png"), full_page=True)
            page.close()
            browser.close()

        print(json.dumps({"etaRanges": True, "responsiveLayouts": 3, "plainLanguage": True,
            "rateLimitMessage": True, "stopPreservesProgressMessage": True,
            "screenshots": str(SCREENSHOTS)}))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

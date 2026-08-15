"""Evening must visibly restart its phases after Morning finishes."""

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
PORT = 8893


def build_status(segment: str, **overrides) -> str:
    value = {"date": "2026-08-14", "state": "not_started", "phase": "waiting",
             "complete": False, "archiveComplete": False, "progress": 0,
             "itemCount": 0, "creatorCount": 0, "elapsedSeconds": 0,
             "metrics": {"elapsedSeconds": 0}, "segment": segment}
    value.update(overrides)
    return json.dumps(value)


with tempfile.TemporaryDirectory(prefix="civitai-full-day-progress-", ignore_cleanup_errors=True) as temporary:
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
            started = {"morning": False, "evening": False}
            evening_polls = {"count": 0}

            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/history/blocks**", lambda route: route.fulfill(status=200,
                content_type="application/json", body=json.dumps({"blocks": {
                    "morning": {"complete": False, "itemCount": 0, "state": "not_started"},
                    "evening": {"complete": False, "itemCount": 0, "state": "not_started"},
                    "all": {"complete": False, "itemCount": 0, "state": "not_started"}}})))
            page.route("**/api/history/estimate**", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"lowSeconds":900,"highSeconds":1200,"fixedBenchmark":true}'))

            def start_route(route):
                segment = route.request.post_data_json["segment"]
                started[segment] = True
                if segment == "morning":
                    body = build_status(segment, state="loading", phase="organizing", progress=100,
                                        itemCount=100, creatorCount=10, elapsedSeconds=10)
                else:
                    body = build_status(segment, state="loading", phase="locating",
                                        elapsedSeconds=0)
                route.fulfill(status=202, content_type="application/json", body=body)

            def status_route(route):
                segment = route.request.url.split("segment=")[-1].split("&")[0]
                if not started.get(segment):
                    body = build_status(segment)
                elif segment == "morning":
                    body = build_status(segment, state="complete", phase="complete", complete=True,
                                        archiveComplete=True, progress=100, itemCount=100,
                                        creatorCount=10, elapsedSeconds=10,
                                        metrics={"elapsedSeconds": 10})
                else:
                    evening_polls["count"] += 1
                    if evening_polls["count"] < 3:
                        body = build_status(segment, state="loading", phase="locating",
                                            elapsedSeconds=evening_polls["count"])
                    else:
                        body = build_status(segment, state="loading", phase="collecting",
                                            progress=20, itemCount=20, creatorCount=3,
                                            elapsedSeconds=evening_polls["count"])
                route.fulfill(status=200, content_type="application/json", body=body)

            page.route("**/api/history/start", start_route)
            page.route("**/api/history/status**", status_route)
            page.route("**/api/history/cancel", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"cancelled":true}'))

            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#buildSetup:not(.hidden)")
            page.locator("#startLoading").click()
            page.locator("#loadingTitle", has_text="Evening").wait_for(timeout=10000)
            page.wait_for_selector("#phaseFinding.active", timeout=10000)
            finding_width = float(page.locator("#progressBar").evaluate(
                "element => parseFloat(element.style.width)"))
            assert 50 < finding_width < 75, finding_width
            page.wait_for_selector("#phaseCollecting.active", timeout=10000)
            collecting_width = float(page.locator("#progressBar").evaluate(
                "element => parseFloat(element.style.width)"))
            assert collecting_width >= finding_width, (finding_width, collecting_width)
            assert not page.locator("#phaseOrganizing").get_attribute("class") == "active"
            page.locator("#stopLoading").click()
            browser.close()
    finally:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close",
                data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(request, timeout=2)
        except Exception:
            process.terminate()
        process.wait(timeout=10)

print({"eveningPhasesRestart": True, "overallProgressContinues": True,
       "morningOrganizeDoesNotMaskEvening": True})

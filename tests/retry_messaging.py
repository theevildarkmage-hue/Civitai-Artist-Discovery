"""A retry must read as a pause, not as a crash.

Civitai returns 5xx intermittently. The app keeps its progress and retries, so the
screen has to say that plainly: keep the real counters, keep the elapsed clock
moving, and save the alarming wording for a service that has actually stopped
answering.
"""

from __future__ import annotations

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
PORT = 8894
SHOTS = ROOT / "reports" / "retry-messaging"
SHOTS.mkdir(parents=True, exist_ok=True)


def response(status: dict) -> str:
    return json.dumps({"date": "2026-07-31", "state": "loading", "progress": 0, "pages": 0,
        "phase": "locating", "itemCount": 0, "creatorCount": 0, "elapsedSeconds": 0,
        "plannedImages": 0, "complete": False, "collectionBackend": "search",
        "retryAttempt": 0, "retryAttempts": 8, **status})


with tempfile.TemporaryDirectory(prefix="civitai-retry-", ignore_cleanup_errors=True) as temporary:
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        current = {"status": response({"state": "not_started", "elapsedSeconds": None})}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/history/start", lambda route: route.fulfill(status=202,
                content_type="application/json", body=current["status"]))
            page.route("**/api/history/status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body=current["status"]))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#startLoading:not(.hidden)")

            def show(status: dict) -> dict:
                current["status"] = response(status)
                page.wait_for_timeout(2200)
                return page.evaluate("""() => ({
                    message: document.getElementById('loadingMessage').textContent,
                    progress: document.getElementById('progressText').textContent,
                    elapsed: document.getElementById('elapsedText').textContent,
                    animated: document.getElementById('progressBar').classList.contains('indeterminate'),
                    waiting: document.getElementById('progressBar').classList.contains('waiting'),
                    title: document.getElementById('loadingTitle').textContent,
                })""")

            current["status"] = response({"elapsedSeconds": 4, "plannedImages": 400})
            page.click("#startLoading")
            page.wait_for_selector("#phaseFinding.active")

            # A first hiccup stays calm and never uses failure language.
            first = show({"elapsedSeconds": 20, "plannedImages": 1200, "pages": 6,
                          "delayReason": "service_retry", "retryInSeconds": 4, "retryAttempt": 1})
            assert "busy" in first["message"], first
            assert "did not respond" not in first["message"], first
            assert "failed" not in first["title"].lower(), first
            # The real counter survives, so the user can see nothing was lost.
            assert "1,200 images to collect" in first["progress"], first
            assert "Retrying in 4s" in first["progress"], first
            # No attempt count while it is still routine.
            assert "attempt" not in first["progress"], first
            assert first["animated"] and first["waiting"], first
            page.screenshot(path=str(SHOTS / "retry-first.png"), full_page=True)

            # The countdown moves between polls rather than sitting frozen.
            second = show({"elapsedSeconds": 23, "plannedImages": 1200, "pages": 6,
                           "delayReason": "service_retry", "retryInSeconds": 1, "retryAttempt": 1})
            assert "Retrying in 1s" in second["progress"], second

            # Only a persistent outage escalates, and it says which attempt it is on.
            persistent = show({"elapsedSeconds": 90, "plannedImages": 1200, "pages": 6,
                               "delayReason": "service_retry", "retryInSeconds": 16, "retryAttempt": 4})
            assert "not responding" in persistent["message"], persistent
            assert "attempt 4 of 8" in persistent["progress"], persistent
            assert "saved" in persistent["message"], persistent
            # The clock keeps running through a retry, so the app never looks frozen.
            assert persistent["elapsed"] != first["elapsed"], (first["elapsed"], persistent["elapsed"])
            page.screenshot(path=str(SHOTS / "retry-persistent.png"), full_page=True)

            # Rate limiting keeps its own explanation at every attempt.
            limited = show({"elapsedSeconds": 120, "plannedImages": 1200, "pages": 6,
                            "delayReason": "rate_limited", "retryInSeconds": 60, "retryAttempt": 5})
            assert "slow down" in limited["message"], limited

            # Recovery clears the retry wording completely.
            recovered = show({"elapsedSeconds": 140, "plannedImages": 2000, "pages": 10,
                              "phase": "collecting", "progress": 30, "itemCount": 5000,
                              "creatorCount": 700, "etaLowSeconds": 60, "etaHighSeconds": 180})
            assert "Retrying" not in recovered["progress"], recovered
            assert "busy" not in recovered["message"] and "not responding" not in recovered["message"], recovered
            assert "5,000 images found from 700 creators" in recovered["progress"], recovered
            assert not recovered["waiting"], recovered

            # A retry while collecting keeps the collected counts on screen too.
            collecting = show({"elapsedSeconds": 160, "phase": "collecting", "progress": 30,
                               "itemCount": 5000, "creatorCount": 700,
                               "delayReason": "service_retry", "retryInSeconds": 8, "retryAttempt": 2})
            assert "5,000 images found from 700 creators" in collecting["progress"], collecting
            assert "Retrying in 8s" in collecting["progress"], collecting
            assert "busy" in collecting["message"], collecting

            assert not errors, errors
            browser.close()

        print({"calmOnFirstRetry": True, "countersPreserved": True, "countdownTicks": True,
               "escalatesOnlyWhenPersistent": True, "rateLimitDistinct": True,
               "recoveryClearsWording": True, "screenshots": str(SHOTS)})
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

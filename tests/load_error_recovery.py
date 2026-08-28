"""A failed build must say it failed consistently, and leave a way back.

Reported directly from a real retry-exhausted failure: the screen showed "History
failed to load" as its title, while the progress line underneath still read "Retrying
now ... attempt 8 of 8" — frozen there from the last poll before the error, since
nothing had ever cleared it. Worse, day navigation stayed disabled the whole time, with
no visible way to try again short of reloading the page.
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
PORT = 8903
SHOTS = ROOT / "reports" / "load-error"
SHOTS.mkdir(parents=True, exist_ok=True)


def response(status: dict) -> str:
    return json.dumps({"date": "2026-08-05", "state": "loading", "progress": 40, "pages": 74,
        "phase": "collecting", "itemCount": 14057, "creatorCount": 1709, "elapsedSeconds": 780,
        "complete": False, "retryAttempt": 8, "retryAttempts": 8, "retryInSeconds": 12,
        "delayReason": "service_retry", **status})


# Seeded with a saved cursor and page count, mirroring the real stuck day this bug was
# found on: real progress existed and a retry should resume it, not restart from zero.
with tempfile.TemporaryDirectory(prefix="civitai-load-error-", ignore_cleanup_errors=True) as temporary:
    from discovery.history import HistoryArchive
    history = HistoryArchive(Path(temporary) / "history")
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,scan_cursor,pages,updated_at) VALUES(?,0,?,?,?)",
                   ("2026-08-05", "21600|1785950281722", 74, "2026-08-06T00:00:00Z"))

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

        current = {"status": response({})}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))

            started = {"count": 0}
            def start_route(route):
                started["count"] += 1
                route.fulfill(status=202, content_type="application/json", body=current["status"])
            page.route("**/api/history/start", start_route)
            page.route("**/api/history/status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body=current["status"]))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#startLoading:not(.hidden)", timeout=20000)

            page.click("#startLoading")
            page.wait_for_selector("#phaseCollecting.active", timeout=10000)
            # Confirm the mid-retry state actually renders the way the report described,
            # so the later "cleared" assertion is proven against a real prior state.
            mid_retry = page.inner_text("#progressText")
            assert "Retrying" in mid_retry and "attempt 8 of 8" in mid_retry, mid_retry
            assert page.eval_on_selector("#olderDay", "n => n.disabled") is True, \
                "navigation should be busy while a build is genuinely in progress"

            # The retry budget is exhausted: the next poll reports a terminal failure.
            current["status"] = response({"state": "error", "errorKind": "service_unavailable",
                "error": "Civitai is still unavailable after 16 attempts. Everything collected so far is saved; Continue building will resume from the last successful page."})
            page.wait_for_selector("#loadingTitle:has-text('Civitai is unavailable')", timeout=10000)
            page.wait_for_timeout(500)

            after = page.inner_text("#progressText")
            assert after == "14,057 images saved in this block.", after
            assert "Retrying" not in after and "attempt" not in after, after
            assert not page.eval_on_selector("#progressBar", "n => n.classList.contains('waiting')")
            assert not page.eval_on_selector("#progressBar", "n => n.classList.contains('indeterminate')")

            # There is a way back: the day, window and view selectors are usable again,
            assert page.eval_on_selector("#olderDay", "n => n.disabled") is False, \
                "navigation was left disabled after the build failed"
            assert page.eval_on_selector("#daySegment", "n => n.disabled") is False
            # and Continue building is offered rather than a dead end.
            assert page.is_hidden("#stopLoading")
            assert page.is_visible("#startLoading") and not page.is_disabled("#startLoading")
            assert page.inner_text("#startLoading") == "Continue building"
            page.screenshot(path=str(SHOTS / "after-failure.png"))

            # Pressing it asks the server to resume, exactly the promise the label makes.
            page.click("#startLoading")
            page.wait_for_timeout(300)
            assert started["count"] == 2, started

            assert not errors, errors
            browser.close()

        print({"outageNamedAccurately": True, "savedCountShown": True,
               "midRetryTextConfirmedFirst": True, "staleRetryTextClearedOnFailure": True,
               "barAnimationCleared": True, "navigationReenabled": True,
               "continueBuildingOffered": True, "retryReachable": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

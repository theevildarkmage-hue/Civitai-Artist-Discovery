"""One failed tag check is retried before a card is written off.

A failing /api/history/tags rejects every card in the batch behind it, and the message
that replaces the artwork stays for the rest of the session. The refresh-token race in
discovery/oauth.py made that happen to whole groups of cards at once, so a single
transient failure must not be the final answer -- but a persistent one still must be
reported rather than retried forever.
"""

import test_safety  # Fail closed before any test network request.

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
PORT = 8903


def artist(name, image_id):
    """The shape the gallery's own card factory consumes: one image, so no carousel."""
    return {"username": name, "imageCount": 1, "representativeIndex": 0,
            "profileUrl": f"https://civitai.red/user/{name}", "avatarUrl": None,
            "following": True, "userId": image_id, "reactedCount": 0,
            "reactedOften": False, "worthFollowing": False, "seen": False,
            "matchedTags": [], "recommendationLabel": None, "recommendationReasons": [],
            "representative": {"id": image_id, "createdAt": "2024-09-11T00:00:00.000Z",
                               "url": f"http://x/{image_id}.jpg",
                               "thumbnailUrl": f"http://x/{image_id}.jpg",
                               "civitaiUrl": f"https://civitai.red/images/{image_id}",
                               "browsingLevel": 1, "stats": {}},
            "seenCount": 0, "knownCount": 5, "complete": True}


with tempfile.TemporaryDirectory(prefix="civitai-tag-retry-", ignore_cleanup_errors=True) as temporary:
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
            page.route("**/api/timemachine", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"cards": [artist("Ana", 1), artist("Bo", 2)],
                                 "status": {"creators": 2, "primed": 2, "images": 10,
                                            "priming": False, "progress": 100}})))

            attempts = []

            def tags(route):
                # Independent visible cards need not enter the same 20ms batch.
                # Count Ana's checks, rather than treating Bo's read as a retry.
                checks_ana = 1 in json.loads(route.request.post_data)['imageIds']
                if checks_ana:
                    attempts.append(time.monotonic())
                # The token refresh behind this endpoint failing is what the server
                # reports as a 500, exactly as it did against the live Civitai API.
                if checks_ana and len(attempts) == 1:
                    route.fulfill(status=500, content_type="application/json",
                                  body='{"error": "HTTP Error 400: Bad Request"}')
                    return
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"images": {"1": {"known": True, "tags": []},
                                                          "2": {"known": True, "tags": []}}}))

            page.route("**/api/history/tags", tags)
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            page.locator(".tm-card", has_text="Ana").wait_for(timeout=10000)

            # The server owns automatic read retries. The browser offers an explicit
            # retry without multiplying the server's budget.
            card = page.locator(".tm-card").first
            # The viewport observer starts the first check. Calling the helper here
            # could itself retry a failure that had already completed.
            card.locator('.tag-check-retry').wait_for()
            assert len(attempts) == 1, f"the first attempt should stand alone: {len(attempts)}"
            assert "tag-check-failed" in (card.get_attribute("class") or ""), \
                "a failed check must be reported"
            page.wait_for_timeout(2000)
            assert len(attempts) == 1, "the browser automatically repeated the server budget"
            card.locator('.tag-check-retry').click()

            # The retry succeeds, so the card ends up with artwork and no message.
            page.wait_for_timeout(2500)
            assert len(attempts) == 2, f"expected exactly one retry, saw {len(attempts)}"
            assert "tag-check-failed" not in (card.get_attribute("class") or ""), \
                "a card that recovered still shows the failure message"
            assert card.get_attribute("data-images-active") == "1", "artwork was never attached"

            page.close()

            # A failure that persists is reported instead of being retried forever. A
            # second page, because the first has already cached its tag results.
            down = []
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/timemachine", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"cards": [artist("Ana", 1)],
                                 "status": {"creators": 1, "primed": 1, "images": 5,
                                            "priming": False, "progress": 100}})))
            page.route("**/api/history/tags", lambda route: (
                down.append(time.monotonic()),
                route.fulfill(status=500, content_type="application/json",
                              body='{"error": "still down"}')))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            page.locator(".tm-card", has_text="Ana").wait_for(timeout=10000)
            page.locator('.tm-card .tag-check-retry').wait_for()
            page.wait_for_timeout(3500)
            assert len(down) == 1, f"a persistent failure must not automatically retry: {len(down)}"
            assert "tag-check-failed" in (page.locator(".tm-card").first.get_attribute("class") or ""), \
                "a card that never verified must say so"

            page.close()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=20)

print({"explicitRetry": True, "noMultipliedBudget": True, "recoveredCardShowsArtwork": True, "persistentFailureReported": True})

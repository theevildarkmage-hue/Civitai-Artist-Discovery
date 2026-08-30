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


def artist(name, image_id, day, *, seen, known, complete):
    """The shape the gallery's own card factory consumes: one image, so no carousel."""
    return {"username": name, "imageCount": 1, "representativeIndex": 0,
            "profileUrl": f"https://civitai.red/user/{name}", "avatarUrl": None,
            "following": True, "userId": image_id, "reactedCount": 0,
            "reactedOften": False, "worthFollowing": False, "seen": False,
            "matchedTags": [], "recommendationLabel": None, "recommendationReasons": [],
            "representative": {"id": image_id, "createdAt": f"{day}T00:00:00.000Z",
                               "url": f"http://x/{image_id}.jpg",
                               "thumbnailUrl": f"http://x/{image_id}.jpg",
                               "civitaiUrl": f"https://civitai.red/images/{image_id}",
                               "browsingLevel": 1, "stats": {}},
            "seenCount": seen, "knownCount": known, "complete": complete}
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
                artist("Ana", 1, "2024-09-11", seen=3, known=177, complete=True),
                artist("Bo", 2, "2025-12-06", seen=0, known=192, complete=False),
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
            assert "3 of 177" in grid and "0 of 192 so far" in grid, grid
            # These are the gallery's cards, not a second implementation: same class,
            # same follow control, and the carousel hidden because there is one image.
            assert page.locator("#timeMachineGrid .creator-card").count() == 2
            assert page.locator("#timeMachineGrid .creator-identity").count() == 2
            arrows = page.locator("#timeMachineGrid .creator-card .next")
            assert arrows.count() == 0 or arrows.first.is_hidden(), "one image needs no carousel"

            # The other tabs still work; gallery chrome is hidden here.
            assert page.locator(".segment-toolbar").is_hidden()
            page.locator("#tabDiscovery").click()
            page.wait_for_selector("#discovery:not(.hidden)")
            assert page.locator("#timeMachine").is_hidden()
            page.locator("#tabGallery").click()
            page.wait_for_selector("#timeMachine", state="hidden")

            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            # A creator finishing mid-browse adds its card without disturbing the rest.
            first = page.locator(".tm-card").first
            page.evaluate("() => { document.querySelector('.tm-card').dataset.probe = 'kept'; }")
            cards.append(artist("Cy", 3, "2025-01-02", seen=0, known=5, complete=True))
            page.evaluate("() => refreshTimeMachine()")
            page.locator(".tm-card", has_text="Cy").wait_for(timeout=10000)
            assert page.locator(".tm-card").count() == 3
            # The untouched card is the same DOM node, not a rebuilt one.
            assert first.get_attribute("data-probe") == "kept", "existing cards must survive"
            # And new cards land in order rather than being appended out of sequence.
            order = page.locator(".tm-card .creator-identity strong").all_inner_texts()
            assert order == sorted(order, key=str.lower), order

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

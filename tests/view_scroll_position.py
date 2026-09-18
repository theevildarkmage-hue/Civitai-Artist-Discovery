"""Each tab keeps its own scroll position.

The tabs share one scrolling page, so the gallery used to inherit wherever the Time
Machine had been left. That drops the reader halfway down a day they never scrolled
through -- and because those cards are then on screen, reading on from there dims work
they never actually passed.
"""

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
PORT = 8907


def tm_card(name, image_id):
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
                               "browsingLevel": 1, "stats": {}, "type": "image"},
            "seenCount": 0, "knownCount": 5, "complete": True}


with tempfile.TemporaryDirectory(prefix="civitai-view-scroll-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
             "width='400' height='600'%3E%3C/svg%3E")
    history = HistoryArchive(Path(temporary) / "history")
    # Enough creators for a page far taller than the viewport, in both tabs.
    items = [{"id": 9800 + n, "postId": 9800 + n, "username": f"Artist{n}",
              "createdAt": f"{day}T13:{n:02d}:00Z", "url": pixel, "width": 400, "height": 600,
              "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}
             for n in range(40)]
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=env)
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

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.route("**/api/timemachine", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"cards": [tm_card(f"Creator{n}", 5000 + n) for n in range(40)],
                                 "status": {"creators": 40, "primed": 40, "images": 200,
                                            "priming": False, "progress": 100}})))
            seen_posts = []
            page.route("**/api/history/seen", lambda route: (
                seen_posts.append(route.request.post_data),
                route.fulfill(status=200, content_type="application/json",
                              body='{"marked": 0}')))

            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":false,"username":"tester"}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"hasData":true,"lastSyncAt":"2099-01-01T00:00:00Z"}'))
            page.route("**/api/creator-metadata**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"creators":{}}'))
            page.route("**/api/reaction-status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"images":{}}'))
            page.route("**/api/history/prepare**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"prepared":false}'))
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=20000)
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(600)
            gallery_at = page.evaluate("() => window.scrollY")
            assert gallery_at > 300, f"the gallery did not scroll: {gallery_at}"

            # Down the Time Machine, well past where the gallery was left.
            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            page.locator(".tm-card").first.wait_for(timeout=15000)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)
            deep = page.evaluate("() => window.scrollY")
            assert deep > gallery_at + 1000, f"the Time Machine did not scroll far enough: {deep}"

            seen_posts.clear()
            page.locator("#tabGallery").click()
            page.wait_for_selector("#gallery:not(.hidden)")
            page.wait_for_timeout(200)
            back = page.evaluate("() => window.scrollY")
            assert abs(back - gallery_at) < 40, \
                f"the gallery opened at {back}, not where it was left ({gallery_at})"

            # Nothing may be dimmed by the switch itself: the reader passed no cards.
            page.wait_for_timeout(2000)
            assert not seen_posts, f"switching tabs marked creators seen: {seen_posts}"
            assert page.locator(".creator-card.is-seen").count() == 0, \
                "switching tabs dimmed cards the reader never scrolled past"

            # And the Time Machine keeps its own place in turn.
            page.locator("#tabTimeMachine").click()
            page.wait_for_selector("#timeMachine:not(.hidden)")
            page.wait_for_timeout(300)
            returned = page.evaluate("() => window.scrollY")
            assert abs(returned - deep) < 40, \
                f"the Time Machine opened at {returned}, not where it was left ({deep})"

            page.close()
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=20)

print({"galleryKeepsItsPlace": True, "timeMachineKeepsItsPlace": True,
       "switchMarksNothingSeen": True})

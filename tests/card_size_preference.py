"""Card size is a saved preference: changing it takes effect immediately and survives
a reload without asking Civitai for anything.
"""

from datetime import datetime, timedelta
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
PORT = 8902

with tempfile.TemporaryDirectory(prefix="civitai-card-size-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{"id": 9900 + n, "postId": 9900 + n, "username": f"Artist{n}",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "Soft", "browsingLevel": 2,
        "stats": {"reactionCount": 1}} for n in range(6)],
        forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,content_rating,updated_at) VALUES(?,1,'X',?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)
    taste = TasteStore(Path(temporary) / "discovery")
    with taste.connect() as db:
        db.executemany("INSERT INTO reacted_images(image_id,first_observed_at,last_observed_at) VALUES(?,?,?)",
                       [(n, "now", "now") for n in (1, 2, 3)])
        db.executemany("INSERT INTO reacted_tags(image_id,tag_id,tag_name) VALUES(?,?,?)",
                       [(n, 1, "favorite-style") for n in (1, 2, 3)])
        db.executemany("INSERT INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                       [(9900 + n, "favorite-style") for n in range(6)])

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
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            errors = []
            dialogs = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))
            civitai_requests = []
            history_starts = []
            page.on("request", lambda request: civitai_requests.append(request.url)
                    if "civitai.com" in request.url or "civitai.red" in request.url else None)
            page.on("request", lambda request: history_starts.append(request.url)
                    if "/api/history/start" in request.url else None)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)
            page.wait_for_selector(".recommendation-badge:has-text('New match')")
            assert "New to you" in page.locator(".recommendation-badge").first.get_attribute("title")

            # Choosing a mature level is itself the explicit opt-in; it must not summon a
            # blocking browser confirmation every time. Returning to the safe level uses
            # the completed local archive and never starts another collection.
            page.click("#contentFilter")
            page.click('#contentMenu [data-level="1"]')
            page.wait_for_selector("#contentFilter:has-text('PG-13')")
            page.wait_for_selector("#contentFilter:enabled")
            page.click("#contentFilter")
            page.click('#contentMenu [data-level="4"]')
            page.wait_for_selector("#contentFilter:has-text('PG-13 + R')")
            page.wait_for_selector("#contentFilter:enabled")
            page.click("#contentFilter")
            page.click('#contentMenu [data-level="4"]')
            page.wait_for_selector("#contentFilter:has-text('PG-13')")
            page.wait_for_selector(".creator-card", timeout=30000)
            assert not dialogs, dialogs
            assert not history_starts, history_starts
            assert page.is_hidden("#loading"), "a local content downgrade looked like a download"

            def card_metrics():
                return page.eval_on_selector(".creator-card", """card => {
                    const stage = card.querySelector('.image-stage').getBoundingClientRect();
                    const strip = card.querySelector('.creator-strip');
                    const stripBox = strip.getBoundingClientRect();
                    const avatar = card.querySelector('.creator-avatar').getBoundingClientRect();
                    const controls = card.querySelector('.creator-controls').getBoundingClientRect();
                    return {stage: stage.height, strip: stripBox.height, avatar: avatar.width,
                        nameFont: parseFloat(getComputedStyle(card.querySelector('.creator-identity strong')).fontSize),
                        controlsInside: controls.right <= stripBox.right + .5 && controls.left >= stripBox.left - .5,
                        noHorizontalOverflow: strip.scrollWidth <= strip.clientWidth + 1};
                }""")

            # Every visible part of a card follows the same Large / Medium / Small scale.
            assert page.eval_on_selector("#cardSize", "n => n.value") == "1"
            large = card_metrics()

            page.select_option("#cardSize", "0.8")
            page.wait_for_timeout(300)
            medium = card_metrics()

            page.select_option("#cardSize", "0.6")
            page.wait_for_timeout(300)
            small = card_metrics()
            assert small["stage"] < medium["stage"] < large["stage"], (small, medium, large)
            assert small["stage"] < large["stage"] * 0.7, (small, large)
            for metric in ("strip", "avatar", "nameFont"):
                assert small[metric] < medium[metric] < large[metric], (metric, small, medium, large)
            assert all(size["controlsInside"] and size["noHorizontalOverflow"]
                       for size in (large, medium, small)), (small, medium, large)

            # SVG chevrons are geometrically centred; font baselines previously made the
            # text glyphs look shifted inside their circles on some systems.
            arrow_alignment = page.eval_on_selector(".carousel-arrow.next", """button => {
                button.hidden = false;
                const circle = button.getBoundingClientRect();
                const icon = button.querySelector('svg').getBoundingClientRect();
                return {x: Math.abs((circle.left + circle.width / 2) - (icon.left + icon.width / 2)),
                        y: Math.abs((circle.top + circle.height / 2) - (icon.top + icon.height / 2)),
                        labelled: button.getAttribute('aria-label') === 'Next image'};
            }""")
            assert arrow_alignment["x"] < .6 and arrow_alignment["y"] < .6, arrow_alignment
            assert arrow_alignment["labelled"], arrow_alignment
            # A pure display preference: no request to Civitai for a smaller box.
            assert not civitai_requests, civitai_requests
            assert page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--card-scale').trim()") == "0.6"

            # It survives a reload.
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)
            assert page.eval_on_selector("#cardSize", "n => n.value") == "0.6"
            reloaded = card_metrics()
            assert abs(reloaded["stage"] - small["stage"]) < 2, (reloaded, small)

            # And a second, fresh tab picks up the same saved preference.
            second = context.new_page()
            second.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            second.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))
            second.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            second.wait_for_selector(".creator-card", timeout=30000)
            assert second.eval_on_selector("#cardSize", "n => n.value") == "0.6"

            assert not errors, errors
            browser.close()

        print({"defaultIsLarge": True, "forYouReasonVisible": True,
               "carouselArrowsCentred": True, "allCardChromeScales": True,
               "allSizesKeepControlsInside": True, "smallerShrinksTheCard": True, "noNetworkCost": True,
               "contentOptInHasNoPopup": True, "independentContentLevels": True,
               "contentDowngradeUsesSavedGallery": True,
               "survivesReload": True, "sharedAcrossTabs": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

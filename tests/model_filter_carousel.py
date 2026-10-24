"""A generation-model filter applies to both card covers and their carousels."""

import test_safety  # Fail closed before any test network request.

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
PORT = 8902


with tempfile.TemporaryDirectory(prefix="civitai-model-filter-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    archive = HistoryArchive(Path(temporary) / "history")
    archive._upsert_normalized([
        {"id": 8101, "postId": 8101, "username": "ModelArtist",
         "createdAt": f"{day}T14:00:00Z", "url": pixel, "width": 8, "height": 8,
         "type": "video", "nsfwLevel": "None", "baseModel": "MiniMax H3YuE2-3B",
         "stats": {"reactionCount": 2}},
        {"id": 8102, "postId": 8102, "username": "ModelArtist",
         "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
         "type": "image", "nsfwLevel": "None", "baseModel": "Illustrious",
         "stats": {"reactionCount": 1}},
    ], forced_date=day)
    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    archive.build_artist_index(day)

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
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body=json.dumps({
                    "connected": True, "socialWrite": False, "username": "tester", "id": 42,
                    "oauthJob": {"state": "complete"}})))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true,"sync":{"running":false}}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card", timeout=15000)

            page.click("#filterToggle")
            page.wait_for_selector('#modelMenu input[value="YuE2-3B"]')
            with page.expect_response(lambda response: "/api/history/artists?" in response.url
                                      and "model=YuE2-3B" in response.url):
                page.check('#modelMenu input[value="YuE2-3B"]')
            page.wait_for_selector(".creator-card")

            page.wait_for_selector('.image-position', timeout=10000)
            page.wait_for_function("()=>document.querySelector('.image-position').textContent.startsWith('1 of 1 images')")
            assert page.is_hidden('.carousel-arrow.next'), 'single matching image should have no carousel arrows'
            images = page.evaluate("""async () => (await fetch('/api/history/artist?date=' + galleryCalendarState().date + '&segment=all&username=ModelArtist&model=YuE2-3B')).json()""")
            assert len(images['images']) == 1 and images['images'][0]['baseModel'] == 'MiniMax H3 | YuE2-3B'
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)

print({"coverFiltered": True, "carouselFiltered": True, "filteredCountAccurate": True})

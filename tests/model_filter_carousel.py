"""A generation-model filter applies to both card covers and their carousels."""

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
         "type": "image", "nsfwLevel": "None", "baseModel": "Pony",
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

            page.click("#modelFilter")
            page.wait_for_selector('#modelMenu input[value="Pony"]')
            with page.expect_response(lambda response: "/api/history/artists?" in response.url
                                      and "model=Pony" in response.url):
                page.check('#modelMenu input[value="Pony"]')
            page.wait_for_selector(".creator-card")

            with page.expect_request(lambda request: "/api/history/artist?" in request.url) as requested:
                page.click(".carousel-arrow.next")
            assert "model=Pony" in requested.value.url, requested.value.url
            deadline = time.monotonic() + 10
            while page.text_content(".image-position") != "1 of 1 images":
                if time.monotonic() > deadline:
                    raise AssertionError(page.text_content(".image-position"))
                page.wait_for_timeout(50)
            assert page.is_hidden(".carousel-arrow.next"), "filtered one-image carousel still has arrows"
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)

print({"coverFiltered": True, "carouselFiltered": True, "filteredCountAccurate": True})

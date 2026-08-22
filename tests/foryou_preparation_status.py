"""For You must say when its tag-backed ranking is still preliminary."""

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
PORT = 8918

with tempfile.TemporaryDirectory(prefix="civitai-foryou-status-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{
        "id": 9810 + index, "postId": 9810 + index, "username": f"Artist{index}",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "Soft", "browsingLevel": 2,
        "stats": {"reactionCount": index + 1},
    } for index in range(4)], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,content_rating,updated_at) VALUES(?,1,'X',?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    # Enough local taste data to enter the ordinary signed-in gallery flow. Archive tags
    # remain deliberately unread so the browser must describe the first ranking as such.
    taste = TasteStore(Path(temporary) / "discovery")
    with taste.connect() as db:
        db.executemany(
            "INSERT INTO reacted_images(image_id,first_observed_at,last_observed_at) VALUES(?,?,?)",
            [(index, "now", "now") for index in (1, 2, 3)],
        )
        db.executemany(
            "INSERT INTO reacted_tags(image_id,tag_id,tag_name) VALUES(?,?,?)",
            [(index, 1, "favorite-style") for index in (1, 2, 3)],
        )

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--port", str(PORT), "--no-browser"],
        cwd=ROOT, env=env,
    )
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
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            complete = [False]
            artist_requests = []

            page.route("**/api/auth-status", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}',
            ))
            page.route("**/api/history/prepare*", lambda route: route.fulfill(
                status=200 if route.request.method == "GET" else 202,
                content_type="application/json",
                body=json.dumps({
                    "kind": "tags", "known": 4 if complete[0] else 1, "total": 4,
                    "complete": complete[0],
                    "job": {"running": not complete[0], "error": None},
                }),
            ))
            page.on("request", lambda request: artist_requests.append(request.url)
                    if "/api/history/artists" in request.url else None)

            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card", timeout=30000)
            banner = page.locator("#followerSweep")
            page.wait_for_selector("#followerSweep.personalization-note.preliminary")
            assert "Rankings are preliminary" in banner.inner_text(), banner.inner_text()
            assert "1 of 4 creator previews analyzed" in banner.inner_text(), banner.inner_text()
            assert banner.locator(".sweep-progress").get_attribute("aria-valuenow") == "25"

            complete[0] = True
            banner.wait_for(state="hidden", timeout=10000)
            assert len(artist_requests) >= 2, artist_requests
            assert page.locator(".creator-card").count() > 0

            # A non-personalized view must cancel the watcher and remove the banner.
            page.select_option("#dayView", "discovery")
            page.wait_for_timeout(200)
            assert banner.is_hidden()
            browser.close()

        print({"preliminaryIsExplicit": True, "progressIsVisible": True,
               "completedTagsRefreshRanking": True, "otherViewsHideBanner": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

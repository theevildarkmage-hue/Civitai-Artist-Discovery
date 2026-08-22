"""Hidden tags gate previews, and a late discovery removes one image without a feed reset."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8919

with tempfile.TemporaryDirectory(prefix="civitai-hidden-preview-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{
        "id": 9700 + index, "postId": 9700 + index, "username": f"Artist{index}",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "Soft", "browsingLevel": 2,
        "stats": {"reactionCount": 10 - index},
    } for index in range(4)], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,content_rating,updated_at) VALUES(?,1,'X',?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)
    taste = TasteStore(Path(temporary) / "discovery")
    with taste.connect() as db:
        db.executemany(
            "INSERT INTO reacted_images(image_id,first_observed_at,last_observed_at) VALUES(?,?,?)",
            [(value, "now", "now") for value in (1, 2, 3)],
        )
        db.executemany(
            "INSERT INTO reacted_tags(image_id,tag_id,tag_name) VALUES(?,?,?)",
            [(value, 1, "favorite") for value in (1, 2, 3)],
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
            phase = ["preview-hidden"]
            artist_requests = []

            page.route("**/api/auth-status", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}',
            ))
            page.route("**/api/history/prepare*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"kind":"tags","known":4,"total":4,"complete":true,"job":{"running":false}}',
            ))

            def tags_route(route):
                ids = json.loads(route.request.post_data or "{}").get("imageIds", [])
                payload = {}
                for image_id in ids:
                    hidden = phase[0] == "preview-hidden" and int(image_id) == 9700
                    payload[str(image_id)] = {"known": True, "tags": [
                        {"name": "blocked-topic" if hidden else "safe-topic", "hidden": hidden}
                    ]}
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"images": payload}))

            page.route("**/api/history/tags", tags_route)

            def detail_route(route):
                image_id = int(parse_qs(urlparse(route.request.url).query)["id"][0])
                hidden = phase[0] == "detail-hidden" and image_id == 9700
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "id": image_id, "thumbnailUrl": pixel, "url": pixel, "civitaiUrl": "#",
                    "width": 8, "height": 8, "createdAt": f"{day}T13:00:00Z",
                    "stats": {"reactionCount": 1}, "known": True,
                    "tags": [{"name": "blocked-topic" if hidden else "safe-topic",
                              "hidden": hidden}],
                }))

            page.route("**/api/history/image?*", detail_route)
            page.on("request", lambda request: artist_requests.append(request.url)
                    if "/api/history/artists" in request.url else None)

            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card[data-id='9701']", timeout=30000)
            page.wait_for_timeout(300)
            assert page.locator(".creator-card[data-id='9700']").count() == 0, \
                "a hidden-tag preview reached the visible feed"
            assert page.locator(".creator-card").count() == 3

            # On a fresh page the preview is initially allowed, then the detail response
            # reveals a newly hidden tag. Only its card may change; the feed and every
            # other card must retain their identity.
            phase[0] = "detail-hidden"
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card[data-id='9700']", timeout=30000)
            survivor = page.locator(".creator-card[data-id='9701']")
            survivor.evaluate("node => window.__survivor = node")
            requests_before = len(artist_requests)
            page.locator(".creator-card[data-id='9700'] .info-button").click()
            page.wait_for_selector("#detailTags .tag-chip.is-hidden")
            page.wait_for_timeout(300)
            assert page.locator(".creator-card[data-id='9700']").count() == 0
            assert page.evaluate("document.body.contains(window.__survivor)"), \
                "opening details rebuilt the feed instead of changing one card"
            assert len(artist_requests) == requests_before, artist_requests
            browser.close()

        print({"hiddenPreviewNeverRendered": True, "tagsBatchedWithPreview": True,
               "detailRemovalIsInPlace": True, "feedWasNotReloaded": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

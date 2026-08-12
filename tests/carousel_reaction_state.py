"""Reaction selections survive carousel navigation and remain image-specific."""

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
PORT = 8883

with tempfile.TemporaryDirectory(prefix="civitai-reaction-state-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    history = HistoryArchive(Path(temporary) / "history")
    value = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='768' height='900'%3E%3C/svg%3E"
    items = [{"id": image_id, "postId": image_id, "username": "CarouselArtist",
        "createdAt": f"{value}T{hour:02d}:00:00Z", "url": pixel, "width": 768,
        "height": 900, "type": "image", "nsfwLevel": "None", "baseModel": "Test",
        "stats": {"likeCount": 0, "heartCount": 0, "laughCount": 0, "cryCount": 0,
            "reactionCount": 0}}
        for image_id, hour in ((8101, 12), (8102, 13), (8103, 14))]
    history._upsert_normalized(items, forced_date=value)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)", (value, datetime.now().isoformat()))
    history.build_artist_index(value)

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT), "--no-browser"], cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 20
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
            page = browser.new_page()
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"connected":true,"username":"Tester","socialWrite":true}'))
            selected: dict[int, set[str]] = {8101: {"Laugh"}, 8102: {"Laugh"}, 8103: {"Laugh"}}

            def reaction_status(route):
                from urllib.parse import parse_qs, urlparse
                ids = [int(value) for value in parse_qs(urlparse(route.request.url).query).get("imageId", [])]
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"images": {
                    str(image_id): {"reactions": sorted(selected.get(image_id, set()))} for image_id in ids}}))

            def reaction(route):
                request = json.loads(route.request.post_data)
                image_id = int(request["imageId"]); name = request["reaction"]
                if name == "Cry":
                    route.fulfill(status=503, content_type="application/json", body='{"error":"test failure"}')
                    return
                names = selected.setdefault(image_id, set())
                names.add(name) if request["active"] else names.discard(name)
                stats = {"likeCount": int("Like" in names), "heartCount": int("Heart" in names),
                    "laughCount": int("Laugh" in names), "cryCount": int("Cry" in names),
                    "reactionCount": len(names)}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"imageId": image_id, "reactions": sorted(names), "stats": stats, "changed": True}))

            page.route("**/api/reaction", reaction)
            page.route("**/api/reaction-status**", reaction_status)
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            card = page.locator(".creator-card")
            card.wait_for()
            first_id = card.get_attribute("data-id")
            card.locator('[data-reaction="Laugh"].selected').wait_for()

            like = card.locator('[data-reaction="Like"]')
            like.click(); page.wait_for_timeout(50)
            assert like.evaluate("element => element.classList.contains('selected')")

            card.locator(".next").click(); page.wait_for_function(
                "([selector, oldId]) => document.querySelector(selector)?.dataset.id !== oldId",
                arg=[".creator-card", first_id])
            second_id = card.get_attribute("data-id")
            assert not card.locator('[data-reaction="Like"]').evaluate("e => e.classList.contains('selected')")
            card.locator('[data-reaction="Heart"]').click(); page.wait_for_timeout(50)
            assert card.locator('[data-reaction="Heart"]').evaluate("e => e.classList.contains('selected')")

            card.locator(".previous").click(); page.wait_for_function(
                "([selector, expected]) => document.querySelector(selector)?.dataset.id === expected",
                arg=[".creator-card", first_id])
            assert card.locator('[data-reaction="Like"]').evaluate("e => e.classList.contains('selected')")
            assert not card.locator('[data-reaction="Heart"]').evaluate("e => e.classList.contains('selected')")

            card.locator('[data-reaction="Like"]').click(); page.wait_for_timeout(50)
            card.locator(".next").click(); page.wait_for_function(
                "([selector, expected]) => document.querySelector(selector)?.dataset.id === expected",
                arg=[".creator-card", second_id])
            card.locator(".previous").click(); page.wait_for_function(
                "([selector, expected]) => document.querySelector(selector)?.dataset.id === expected",
                arg=[".creator-card", first_id])
            assert not card.locator('[data-reaction="Like"]').evaluate("e => e.classList.contains('selected')")

            card.locator('[data-reaction="Cry"]').click(); page.wait_for_timeout(100)
            assert not card.locator('[data-reaction="Cry"]').evaluate("e => e.classList.contains('selected')")
            assert "test failure" in page.locator("#toast").inner_text()
            browser.close()
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close",
                data=b"{}", method="POST", headers={"Content-Type": "application/json"}), timeout=2)
        except Exception:
            pass
        process.wait(timeout=10)

print(json.dumps({"revisitPreserved": True, "removalPreserved": True,
    "imagesIndependent": True, "failureDidNotMutate": True}))

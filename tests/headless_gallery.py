"""Clean-profile browser test with no dependency on developer data or identity."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCREENSHOTS = ROOT / "archive" / "validation_2026-08-01"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
port = 8877

with tempfile.TemporaryDirectory(prefix="civitai-history-test-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    history = HistoryArchive(Path(temporary) / "history")
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='768' height='900'%3E%3Crect width='100%25' height='100%25' fill='%232b5360'/%3E%3C/svg%3E"
    items = []
    for creator in range(120):
        for variant in range(3 if creator == 0 else 1):
            image_id = creator * 10 + variant + 1
            items.append({"id": image_id, "postId": image_id, "username": f"Artist_{creator:03d}",
                "createdAt": f"{yesterday}T12:{creator % 60:02d}:00Z", "url": pixel, "width": 768, "height": 900,
                "type": "image", "nsfwLevel": "None", "baseModel": "Test", "stats": {"reactionCount": creator % 9}})
    history._upsert_normalized(items, forced_date=yesterday)
    with history.connect() as db:
        db.execute("INSERT OR REPLACE INTO days(day,complete,updated_at) VALUES(?,1,?)", (yesterday, datetime.now().isoformat()))
    history.build_artist_index(yesterday)

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(port), "--no-browser"], cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth-status", timeout=1) as response:
                    assert json.loads(response.read())["connected"] is False
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/reaction", data=b'{"imageId":1,"reaction":"Like","active":true}', method="POST", headers={"Content-Type":"application/json"})
                try: urllib.request.urlopen(request, timeout=2); raise AssertionError("read-only write unexpectedly succeeded")
                except urllib.error.HTTPError as error: assert error.code == 403
                break
            except Exception:
                if time.monotonic() > deadline: raise
                time.sleep(.2)

        errors = []
        requests = []
        archived = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/history/status?date={yesterday}&segment=all",
            timeout=5).read())
        assert archived["complete"] and archived["itemCount"] == len(items), archived
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" and "Failed to load resource" not in msg.text else None)
            page.on("pageerror", lambda error: errors.append(f"page:{error}"))
            page.on("request", lambda request: requests.append(request.url))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200, content_type="application/json", body='{"connected":true,"username":"portable-test","socialWrite":true}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/reaction", lambda route: route.fulfill(status=200, content_type="application/json", body='{"reactions":["Like"],"stats":{"likeCount":1,"reactionCount":1}}'))
            page.route("**/api/follow", lambda route: route.fulfill(status=200, content_type="application/json", body='{"userId":123,"following":true}'))
            rebuilds = []
            def rebuild(route):
                rebuilds.append(json.loads(route.request.post_data))
                route.fulfill(status=202, content_type="application/json", body=json.dumps({
                    "complete": True, "archiveComplete": True, "rebuilding": True,
                    "state": "complete", "phase": "complete", "itemCount": len(items),
                    "creatorCount": 120, "metrics": {"elapsedSeconds": 1}}))
            page.route("**/api/history/rebuild", rebuild)
            page.route("**/api/history/start", rebuild)
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle", timeout=30000)
            try:
                page.wait_for_selector(".creator-card")
            except PlaywrightTimeoutError as error:
                raise AssertionError({"loadingTitle": page.locator("#loadingTitle").inner_text(),
                    "loadingMessage": page.locator("#loadingMessage").inner_text(),
                    "segment": page.locator("#daySegment").input_value(),
                    "consoleErrors": errors, "requests": requests[-20:]}) from error
            assert page.locator("#daySegment").input_value() == "all"
            assert page.locator("#rebuildDay").is_enabled()
            page.locator("#rebuildDay").click()
            page.wait_for_selector(".creator-card")
            deadline = time.monotonic() + 5
            while len(rebuilds) < 2 and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert [request["segment"] for request in rebuilds] == ["morning", "evening"], rebuilds
            assert all(request["date"] == yesterday for request in rebuilds)
            initial = page.locator(".creator-card").count()
            first = page.locator(".creator-card").first
            before = first.get_attribute("data-id")
            if not first.locator(".next").is_hidden():
                first.locator(".next").click()
                page.locator(f'.creator-card:not([data-id="{before}"])').first.wait_for()
            first.locator("[data-reaction]").first.click()
            page.locator("#toast", has_text="reaction added").wait_for()
            first.locator(".total-reactions", has_text="Total 1").wait_for()
            assert first.locator(".total-reactions").get_attribute("title") == "Total reactions"
            follow = page.locator(".follow-button:not(.is-following)").first
            follow.click()
            page.locator(".follow-button.is-following").first.wait_for()
            page.mouse.wheel(0, 100000)
            page.locator(".creator-card").nth(50).wait_for()
            after_scroll = page.locator(".creator-card").count()
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".creator-card")
            after_reload = page.locator(".creator-card").count()
            page.screenshot(path=str(SCREENSHOTS / "portable-clean-profile.png"))
            page.locator("#daySegment").select_option("evening")
            page.locator("#startLoading").wait_for()
            assert page.locator("#rebuildDay").is_disabled()
            browser.close()
        # Reloading restores where you were rather than starting over, so the card
        # count after a reload is at least what had been loaded before it.
        assert initial == 50 and after_scroll > initial and after_reload >= 50
        assert not errors, errors
        assert not any("buzz" in url.casefold() for url in requests)
        print({"readOnlyStartup": True, "initialCards": initial, "afterScroll": after_scroll, "afterReload": after_reload, "errors": errors})
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill()

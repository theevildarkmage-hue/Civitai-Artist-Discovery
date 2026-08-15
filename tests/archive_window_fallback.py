"""Navigation opens an available legacy All-day archive instead of an empty block."""

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
PORT = 8882

with tempfile.TemporaryDirectory(prefix="civitai-window-fallback-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    history = HistoryArchive(Path(temporary) / "history")
    newest = (datetime.now() - timedelta(days=1)).date()
    legacy = newest - timedelta(days=1)
    value = legacy.isoformat()
    item = {"id": 7001, "postId": 7001, "username": "ArchivedArtist",
        "createdAt": f"{value}T12:00:00Z", "url": "data:image/svg+xml,%3Csvg/%3E",
        "width": 768, "height": 900, "type": "image", "nsfwLevel": "None",
        "baseModel": "Test", "stats": {"reactionCount": 1}}
    history._upsert_normalized([item], forced_date=value)
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
            # The gallery is behind sign-in now, so this test signs in to reach it.
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#startLoading:not(.hidden)")
            assert page.locator("#daySegment").input_value() == "evening"

            # All day queues the two halves; it is still published locally rather than
            # collected as a duplicate third network job.
            assert page.locator('#buildRange [data-segment="all"]').get_attribute("class") == "selected"
            assert page.locator("#startLoading").is_visible()
            assert page.locator("#startLoading").inner_text() == "Build gallery"
            assert "two resumable halves" in page.locator('#buildRange [data-segment="all"]').inner_text()

            page.locator("#olderDay").click()
            page.wait_for_selector(".creator-card")
            assert page.locator("#daySegment").input_value() == "all"
            assert "ArchivedArtist" in page.locator(".creator-card").inner_text()

            # A remembered half-day is browsing position, not the preferred window on
            # the next launch. If the completed all-day union exists, it wins.
            page.evaluate("""value => sessionStorage.setItem('civitai-feed-state', JSON.stringify({
                date:value, segment:'evening', view:'foryou', models:[], loaded:50, scrollY:200
            }))""", value)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card")
            assert page.locator("#daySegment").input_value() == "all"

            # An explicit user selection remains explicit; it may be built separately.
            page.locator("#daySegment").select_option("evening")
            page.wait_for_selector("#startLoading:not(.hidden)")
            page.locator('#buildRange [data-segment="evening"]').click()
            assert page.locator('#buildRange [data-segment="evening"]').get_attribute("class") == "selected"
            assert page.locator("#startLoading").inner_text() == "Build gallery"
            browser.close()
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/app/close", data=b"{}", method="POST", headers={"Content-Type": "application/json"}), timeout=2)
        except Exception:
            pass
        process.wait(timeout=10)

print(json.dumps({"legacyAllDayOpenedOnNavigation": True, "readyAllDayOverridesSavedHalf": True,
                  "explicitSegmentPreserved": True,
                  "allDayBuiltFromHalvesOnly": True}))

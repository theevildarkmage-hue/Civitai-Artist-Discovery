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

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{"id": 9900 + n, "postId": 9900 + n, "username": f"Artist{n}",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}} for n in range(6)],
        forced_date=day)
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
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))
            civitai_requests = []
            page.on("request", lambda request: civitai_requests.append(request.url)
                    if "civitai.com" in request.url or "civitai.red" in request.url else None)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)

            # Default is Large: matches the height this gallery has always used.
            assert page.eval_on_selector("#cardSize", "n => n.value") == "1"
            large = page.eval_on_selector(".image-stage", "n => n.getBoundingClientRect().height")

            page.select_option("#cardSize", "0.6")
            page.wait_for_timeout(300)
            small = page.eval_on_selector(".image-stage", "n => n.getBoundingClientRect().height")
            assert small < large * 0.7, (small, large)
            # A pure display preference: no request to Civitai for a smaller box.
            assert not civitai_requests, civitai_requests
            assert page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--card-scale').trim()") == "0.6"

            # It survives a reload.
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)
            assert page.eval_on_selector("#cardSize", "n => n.value") == "0.6"
            reloaded = page.eval_on_selector(".image-stage", "n => n.getBoundingClientRect().height")
            assert abs(reloaded - small) < 2, (reloaded, small)

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

        print({"defaultIsLarge": True, "smallerShrinksTheCard": True, "noNetworkCost": True,
               "survivesReload": True, "sharedAcrossTabs": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

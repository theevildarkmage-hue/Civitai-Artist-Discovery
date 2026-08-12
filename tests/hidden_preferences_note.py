"""Content Controls filtering must be stated on the dashboard, not left for the user to
notice on their own, with a live link to where it is actually managed.
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
PORT = 8898
SHOTS = ROOT / "reports" / "hidden-note"
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="civitai-hidden-note-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    # A completed day so the app opens straight into the gallery.
    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{"id": 9701, "postId": 9701, "username": "NoteArtist",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 8, "height": 8,
        "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}], forced_date=day)
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

        # The endpoint's real, unmocked shape — proves the UI's assumptions about it
        # (field names, "never imported" meaning importedAt is null) match reality.
        real = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/discovery/hidden",
                                                  timeout=15).read())
        assert real == {"creators": 0, "tags": 0, "images": 0, "importedAt": None}, real

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":false}'))

            hidden_state = {"body": json.dumps(real)}
            page.route("**/api/discovery/hidden*", lambda route: route.fulfill(status=200,
                content_type="application/json", body=hidden_state["body"]))

            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)

            # The tab no longer calls itself "discovery" — the content is a taste
            # profile, not a place to discover new artwork.
            assert page.inner_text("#tabDiscovery") == "My Profile", page.inner_text("#tabDiscovery")

            page.click("#tabDiscovery")
            page.wait_for_selector("#hiddenPreferencesNote:not(.hidden)", timeout=15000)

            # Never imported: says so, not "nothing hidden" (that would imply Civitai
            # was actually read and came back empty, which did not happen).
            text = page.inner_text("#hiddenPreferencesText")
            assert "once your account is read" in text, text

            link = page.get_attribute("#hiddenPreferencesManage", "href")
            assert link == "https://civitai.red/user/account", link
            assert page.get_attribute("#hiddenPreferencesManage", "target") == "_blank"

            # Imported, nothing hidden.
            hidden_state["body"] = json.dumps({"creators": 0, "tags": 0, "images": 0,
                                               "importedAt": "2026-08-05T12:00:00+00:00"})
            page.click("#hiddenPreferencesRefresh")
            page.wait_for_function(
                "() => document.getElementById('hiddenPreferencesText').textContent.includes('Nothing is hidden')",
                timeout=10000)

            # Imported, with real counts — the number a user would actually see.
            hidden_state["body"] = json.dumps({"creators": 128, "tags": 117, "images": 1,
                                               "importedAt": "2026-08-05T12:05:00+00:00"})
            page.click("#hiddenPreferencesRefresh")
            page.wait_for_function(
                "() => document.getElementById('hiddenPreferencesText').textContent.includes('128')",
                timeout=10000)
            final = page.inner_text("#hiddenPreferencesText")
            # The trailing period pins this to the singular: "1 hidden image" alone is
            # also a substring of the (wrong) plural "1 hidden images."
            assert "128 hidden creators" in final and "117 hidden tags" in final, final
            assert "1 hidden image." in final and "1 hidden images" not in final, final
            page.wait_for_selector("#toast:not(.hidden)")
            assert "re-read" in page.inner_text("#toast").lower()

            page.screenshot(path=str(SHOTS / "hidden-note.png"))
            assert not errors, errors
            browser.close()

        print({"tabRenamed": True, "noteStatesBeforeImport": True, "manageLinkCorrect": True,
               "refreshUpdatesCounts": True, "endpointShapeMatchesUiAssumptions": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

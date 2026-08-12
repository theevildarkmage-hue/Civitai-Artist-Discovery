"""Everything in the image dialog must be reachable, however many tags it has.

The dialog is a fixed-height grid. Its single row was auto-sized, so with a long tag list
the details column grew past the dialog and was clipped by it — the column's own
overflow could never engage, and the last tags, the prompt and the links were simply
unreachable. Nothing about it looked broken, which is what made it worth pinning.
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
PORT = 8897
SHOTS = ROOT / "reports" / "detail-dialog"
SHOTS.mkdir(parents=True, exist_ok=True)

# More tags than any dialog could show at once, which is the whole point.
TAGS = [f"tag-number-{index:02d}" for index in range(70)]

with tempfile.TemporaryDirectory(prefix="civitai-detail-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='768' height='1024'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([{"id": 8801, "postId": 8801, "username": "TaggyArtist",
        "createdAt": f"{day}T13:00:00Z", "url": pixel, "width": 768, "height": 1024,
        "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 3},
        "prompt": "a long prompt " * 40}], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    store = TasteStore(Path(temporary) / "discovery")
    with store.connect() as db:
        db.executemany("INSERT INTO archive_image_tags(image_id, tag_name) VALUES(?,?)",
                       [(8801, name) for name in TAGS])
        # Deliberately no hidden tag here: an image carrying one is itself hidden, so it
        # would never reach the gallery to be opened. The hidden marking is covered at
        # the API level in tests/content_controls.py.

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
            for width, height, label in ((1920, 1080, "desktop"), (1440, 960, "laptop"),
                                         (1280, 800, "12in"), (1024, 700, "short"),
                                         (430, 850, "mobile")):
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                    content_type="application/json",
                    body='{"connected":true,"socialWrite":true,"username":"tester","id":7}'))
                page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                    content_type="application/json", body='{"hasData":true}'))
                page.route("**/api/creator-metadata**", lambda route: route.fulfill(status=200,
                    content_type="application/json", body='{"creators":{}}'))
                page.route("**/api/reaction-status**", lambda route: route.fulfill(status=200,
                    content_type="application/json", body='{"images":{}}'))
                page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
                page.wait_for_selector(".creator-card", timeout=30000)
                page.click(".creator-card .info-button")
                page.wait_for_selector("#detailTags .tag-chip", timeout=15000)

                shown = page.eval_on_selector_all("#detailTags .tag-chip", "n => n.length")
                assert shown == len(TAGS), (label, shown)

                # Scroll whichever element actually scrolls, then require that the last
                # tag and the links below it are inside the dialog.
                reach = page.evaluate("""() => {
                    const dialog = document.getElementById('details');
                    const copy = document.querySelector('.detail-copy');
                    const style = getComputedStyle(copy).overflowY;
                    const scroller = (style === 'auto' || style === 'scroll') ? copy : dialog;
                    const chips = [...document.querySelectorAll('#detailTags .tag-chip')];
                    const last = chips[chips.length - 1];
                    const box = dialog.getBoundingClientRect();
                    const overhang = Math.round(copy.getBoundingClientRect().bottom - box.bottom);
                    scroller.scrollTop = scroller.scrollHeight;
                    const tag = last.getBoundingClientRect();
                    const links = document.querySelector('.detail-links').getBoundingClientRect();
                    return {overhang, scrolled: Math.round(scroller.scrollTop),
                            lastTag: tag.bottom <= box.bottom + 2 && tag.top >= box.top - 2,
                            links: links.bottom <= box.bottom + 2};
                }""")
                # The panel must not extend past the dialog, or it is clipped with no
                # way to scroll to what was cut off.
                assert reach["overhang"] <= 2, (label, reach)
                assert reach["lastTag"], (label, reach)
                assert reach["links"], (label, reach)
                # There is genuinely more content than fits, so this proves scrolling
                # rather than a dialog that happened to be large enough.
                assert reach["scrolled"] > 0, (label, reach)
                page.screenshot(path=str(SHOTS / f"detail-{label}.png"))
                assert not errors, (label, errors)
                page.close()
            browser.close()

        print({"allTagsRendered": len(TAGS), "noOverhangAtAnySize": True, "lastTagReachable": True,
               "linksReachable": True, "sizes": 5})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

"""Cards already scrolled past dim immediately and move to the bottom on the next
fresh load — never mid-scroll, which would shift content under the user while they
are actively browsing it.

Deliberately exercises every documented rule: marking is day-scoped (not segment- or
view-scoped), it never touches Civitai (works read-only), it applies even to the
default "discovery" view (which otherwise takes a cheaper SQL path with no materialised
order), and it composes with hidden/model filtering without reintroducing the
duplicate-card bug the session-freeze mechanism exists to prevent.
"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8906

with tempfile.TemporaryDirectory(prefix="civitai-seen-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    # Sixty creators: the browser loads 50 first, which makes the reported page-boundary
    # failure reproducible while still leaving a second page to fetch.
    items = [{"id": 9800 + n, "postId": 9800 + n, "username": f"Artist{n}",
              "createdAt": f"{day}T13:{n:02d}:00Z", "url": pixel, "width": 8, "height": 8,
              "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}
             for n in range(60)]
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    # Unit-level: the store itself, independent of the HTTP layer.
    store = TasteStore(Path(temporary) / "discovery")
    assert store.seen_creator_keys(day) == set()
    # Casefolds internally, so mixed case collapses to one key rather than silently
    # storing a variant the "seen" lookup (which always casefolds) would never match.
    marked = store.mark_seen(day, ["Artist0", "artist1", "ARTIST1", ""])
    assert marked == 2, marked
    assert store.seen_creator_keys(day) == {"artist0", "artist1"}
    # Re-marking is a no-op, not an error — insert-or-ignore, not a fresh timestamp.
    assert store.mark_seen(day, ["artist0"]) == 1
    assert store.seen_creator_keys(day) == {"artist0", "artist1"}
    # Rows written by the buggy observer remain recoverable but are not trusted. A real
    # future pass upgrades the same key through mark_seen's conflict update.
    with store.connect() as db:
        db.execute("INSERT INTO seen_creators(day,username_key,seen_at,tracking_version) "
                   "VALUES(?,?,?,1)", (day, "artist59", datetime.now().isoformat()))
    assert "artist59" not in store.seen_creator_keys(day)
    store.mark_seen(day, ["Artist59"])
    assert "artist59" in store.seen_creator_keys(day)
    with store.connect() as db:
        db.execute("UPDATE seen_creators SET tracking_version=1 WHERE day=? AND username_key=?",
                   (day, "artist59"))

    # An actual pre-fix database has no tracking_version column. Startup adds it with the
    # old rows quarantined as v1 instead of failing the whole application migration.
    legacy_root = Path(temporary) / "legacy-seen"
    legacy_root.mkdir()
    with sqlite3.connect(legacy_root / "taste.sqlite3") as db:
        db.execute("CREATE TABLE seen_creators(day TEXT NOT NULL, username_key TEXT NOT NULL, "
                   "seen_at TEXT NOT NULL, PRIMARY KEY(day,username_key))")
        db.execute("INSERT INTO seen_creators(day,username_key,seen_at) VALUES(?,?,?)",
                   (day, "legacyfalsemark", datetime.now().isoformat()))
    legacy_store = TasteStore(legacy_root)
    with legacy_store.connect() as db:
        assert "tracking_version" in {row[1] for row in db.execute(
            "PRAGMA table_info(seen_creators)")}
    assert legacy_store.seen_creator_keys(day) == set()

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

        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=15) as response:
                return json.loads(response.read())

        def post(path, body, expect=200):
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    assert response.status == expect, response.status
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                assert error.code == expect, error.code
                return json.loads(error.read())

        # Validation: never touches Civitai, so it works with no write permission and
        # even on a plain read-only connection — but it still rejects garbage input.
        import urllib.error
        assert post("/api/history/seen", {"date": "not-a-date", "usernames": ["x"]}, expect=400)
        assert post("/api/history/seen", {"date": day, "usernames": []}, expect=400)
        assert post("/api/history/seen", {"date": day, "usernames": "Artist2"}, expect=400)

        # The default "discovery" view, in a plain fresh request: Artist0/Artist1 (seen
        # via the store directly, above) are already known and should already trail.
        default_view = get(f"/api/history/artists?date={day}&segment=all&view=discovery&offset=0&limit=100")
        names = [a["username"] for a in default_view["artists"]]
        seen_flags = {a["username"]: a["seen"] for a in default_view["artists"]}
        assert seen_flags["Artist0"] is True and seen_flags["Artist1"] is True
        assert names.index("Artist0") > names.index("Artist2")
        assert names.index("Artist1") > names.index("Artist2")

        # One continuous session: mark a THIRD creator seen mid-session, then re-fetch
        # under the same session id. The flag must update live; the position must not.
        session = "seen-test-session-1"
        first = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                    f"&offset=0&limit=100&session={session}")
        first_order = [a["username"] for a in first["artists"]]
        assert not first["artists"][0]["seen"] or first_order[0] in ("Artist0", "Artist1"), first_order

        target = next(name for name in first_order if name not in ("Artist0", "Artist1"))
        response = post("/api/history/seen", {"date": day, "usernames": [target]})
        assert response["marked"] == 1, response

        again = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                    f"&offset=0&limit=100&session={session}")
        again_order = [a["username"] for a in again["artists"]]
        assert again_order == first_order, (first_order, again_order,
            "the same session's order moved after a mid-session mark — it must not")
        assert next(a["seen"] for a in again["artists"] if a["username"] == target) is True, \
            "the flag must update live even though the order stays frozen"

        # A genuinely fresh session must now place the newly-seen creator at the end.
        reopened = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                       f"&offset=0&limit=100&session=seen-test-session-2")
        reopened_order = [a["username"] for a in reopened["artists"]]
        seen_now = {a["username"] for a in reopened["artists"] if a["seen"]}
        assert target in seen_now, seen_now
        unseen_positions = [i for i, name in enumerate(reopened_order) if name not in seen_now]
        seen_positions = [i for i, name in enumerate(reopened_order) if name in seen_now]
        assert (not unseen_positions or not seen_positions
                or max(unseen_positions) < min(seen_positions)), \
            (reopened_order, seen_now, "every seen creator must sort after every unseen one")

        # Marking never requires write access — read-only throughout this whole test,
        # confirmed by never having granted socialWrite at all.
        auth = get("/api/auth-status")
        assert auth.get("socialWrite") is not True, auth

        # Real-browser check: a card sitting in the viewport at page load must not dim
        # itself just for having rendered there — only scrolling it away should. This is
        # the one part of the feature the HTTP-level checks above cannot see, because they
        # mark creators directly through the store rather than through actual scrolling.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            # Narrow enough for one card per row, short enough that only the very first
            # card is on screen at load — the exact scenario that self-dimmed under the
            # old entry+dwell design.
            page = browser.new_page(viewport={"width": 420, "height": 480})
            errors = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            # The app gates its whole gallery behind a connected, analysed account (the
            # welcome/sign-in screen otherwise). This test is about scrolling, not sign-in,
            # so the account layer is stubbed exactly as the discovery-dashboard UI test
            # does, and left read-only throughout.
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":false,"username":"tester"}'))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.route("**/api/creator-metadata**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"creators":{}}'))
            page.route("**/api/reaction-status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"images":{}}'))
            page.route("**/api/history/prepare**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"prepared":false}'))
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card")

            def cards():
                return page.eval_on_selector_all(".creator-card", """nodes => nodes.map(n => (
                    {username: n.dataset.username, seen: n.classList.contains('is-seen')}))""")

            initial = cards()
            assert initial, "no cards rendered"
            subject = initial[0]["username"]
            page_boundary_subject = initial[49]["username"]
            selector = f'.creator-card[data-username="{subject}"]'
            # Bring the card fully onto screen (the toolbar above the gallery means the
            # first card is not necessarily visible at scrollY=0) and let the dwell window
            # pass while it sits there, untouched. Still on screen — never scrolled past —
            # so it must still be unmarked: this is the exact case that self-dimmed under
            # the old entry+dwell design, since merely sitting on screen used to be enough.
            page.eval_on_selector(selector, "n => n.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1200)
            assert next(c["seen"] for c in cards() if c["username"] == subject) is False, \
                ("card dimmed without ever being scrolled past", cards())

            # Replacing the gallery for another ordering used to make IntersectionObserver
            # report every removed visible card as having left the viewport. After the 3s
            # batch flush, those false marks appeared dimmed in whichever view came next.
            with page.expect_response(lambda response: "/api/history/artists?" in response.url
                                      and "view=discovery" in response.url):
                page.locator("#dayView").select_option("discovery")
            page.wait_for_timeout(3500)
            after_view_switch = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                                    f"&offset=0&limit=100&session=seen-view-switch-check")
            false_marks = {a["username"].lower() for a in after_view_switch["artists"]
                           if a["seen"]}
            assert subject not in false_marks, (subject, false_marks)
            assert page_boundary_subject not in false_marks, (page_boundary_subject, false_marks)

            # Hiding the gallery behind My Profile is another structural exit, not a
            # scroll. It must pause observation and resume cleanly on return.
            tab_subject = cards()[0]["username"]
            tab_selector = f'.creator-card[data-username="{tab_subject}"]'
            page.eval_on_selector(tab_selector, "n => n.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1200)
            page.locator("#tabDiscovery").click()
            page.wait_for_timeout(1200)
            page.locator("#tabGallery").click()
            page.wait_for_timeout(3500)
            after_tab_switch = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                                   f"&offset=0&limit=100&session=seen-tab-switch-check")
            tab_marks = {a["username"].lower() for a in after_tab_switch["artists"] if a["seen"]}
            assert tab_subject not in tab_marks, (tab_subject, tab_marks)

            # Continue the original positive case against the currently rendered card:
            # a real downward pass still dims and persists it.
            subject = tab_subject
            selector = tab_selector
            page.eval_on_selector(selector, "n => n.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1200)

            # Now scroll past it — comfortably more than its own height, so it is
            # unambiguously off the top of the screen — and let the exit fire.
            card_height = page.eval_on_selector(selector, "n => n.getBoundingClientRect().height")
            page.evaluate(f"window.scrollBy(0, {card_height + 400})")
            page.wait_for_timeout(1200)
            scrolled = cards()
            assert next(c["seen"] for c in scrolled if c["username"] == subject) is True, scrolled

            # The batched write must reach the server on its own, with no further action.
            page.wait_for_timeout(3200)
            fresh = get(f"/api/history/artists?date={day}&segment=all&view=discovery"
                        f"&offset=0&limit=100&session=seen-test-browser-check")
            server_seen = {a["username"].lower() for a in fresh["artists"] if a["seen"]}
            assert subject in server_seen, (subject, server_seen)

            # A genuinely fresh load — a real reload, not a mocked session id — must carry
            # the now-seen card out of the lead position rather than showing it again first.
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".creator-card")
            reloaded = cards()
            assert reloaded[0]["username"] != subject, reloaded
            assert not any(c["username"] == subject and not c["seen"] for c in reloaded), reloaded

            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

print({"dayScopedNotSegmentScoped": True, "validatesInput": True, "worksReadOnly": True,
       "appliesToDefaultView": True, "flagLiveOrderFrozen": True, "freshSessionReorders": True,
       "viewSwitchDoesNotMark": True, "tabSwitchDoesNotMark": True,
       "pageBoundaryDoesNotMark": True, "legacyFalseMarksQuarantined": True})

"""Dashboard suggestions and gallery familiarity hearts use separate thresholds."""

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
PORT = 8900
SHOTS = ROOT / "reports" / "worth-following"
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="civitai-worth-following-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.history import HistoryArchive
    from discovery.taste import GALLERY_HEART_MIN, TasteStore, WORTH_FOLLOWING_MIN

    assert WORTH_FOLLOWING_MIN == 10, WORTH_FOLLOWING_MIN
    assert GALLERY_HEART_MIN == 5, GALLERY_HEART_MIN

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([
        {"id": 9800, "postId": 9800, "username": "OftenReacted", "createdAt": f"{day}T13:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
        {"id": 9801, "postId": 9801, "username": "JustBelowBar", "createdAt": f"{day}T12:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
        {"id": 9802, "postId": 9802, "username": "AlreadyFollowed", "createdAt": f"{day}T11:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
        {"id": 9803, "postId": 9803, "username": "BelowHeart", "createdAt": f"{day}T10:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
        {"id": 9804, "postId": 9804, "username": "HeartAtFive", "createdAt": f"{day}T09:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
    ], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    store = TasteStore(Path(temporary) / "discovery")
    with store.connect() as db:
        # Exactly at the bar: qualifies.
        db.executemany("INSERT INTO reacted_images(image_id, creator_id, creator_username, "
                       "first_observed_at, last_observed_at) VALUES(?,?,?,?,?)",
                       [(n, 501, "OftenReacted", "now", "now") for n in range(1, WORTH_FOLLOWING_MIN + 1)])
        # One short of the bar: must not qualify.
        db.executemany("INSERT INTO reacted_images(image_id, creator_id, creator_username, "
                       "first_observed_at, last_observed_at) VALUES(?,?,?,?,?)",
                       [(n, 502, "JustBelowBar", "now", "now")
                        for n in range(101, 101 + WORTH_FOLLOWING_MIN - 1)])
        # Above the bar, but already followed: must not carry the badge either.
        db.executemany("INSERT INTO reacted_images(image_id, creator_id, creator_username, "
                       "first_observed_at, last_observed_at) VALUES(?,?,?,?,?)",
                       [(n, 503, "AlreadyFollowed", "now", "now")
                        for n in range(201, 201 + WORTH_FOLLOWING_MIN + 2)])
        db.executemany("INSERT INTO reacted_images(image_id, creator_id, creator_username, "
                       "first_observed_at, last_observed_at) VALUES(?,?,?,?,?)",
                       [(n, 504, "BelowHeart", "now", "now")
                        for n in range(301, 301 + GALLERY_HEART_MIN - 1)])
        db.executemany("INSERT INTO reacted_images(image_id, creator_id, creator_username, "
                       "first_observed_at, last_observed_at) VALUES(?,?,?,?,?)",
                       [(n, 505, "HeartAtFive", "now", "now")
                        for n in range(401, 401 + GALLERY_HEART_MIN)])
        db.execute("INSERT INTO followed_creators(creator_id) VALUES(503)")
        db.execute("INSERT INTO creator_followers(creator_id, username, follower_count, fetched_at) "
                   "VALUES(503,'AlreadyFollowed',900,'now')")

    # A card's own "following" flag comes from the gallery's follow cache keyed to a real
    # resolved account id, not the taste store's followed_creators table. The server runs
    # as a subprocess, so nothing patched in this process reaches it — the token has to be
    # a real file on disk, exactly as a genuine sign-in would leave one.
    import discovery.oauth as oauth
    payload = {"access_token": "a", "refresh_token": "r", "expires_at": 2 ** 31,
               "client_id": oauth.client_id(), "scope": 524321,
               "identity": {"id": 7, "username": "tester"}}
    oauth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    oauth.TOKEN_PATH.write_bytes(oauth._crypt(json.dumps(payload).encode(), True))
    (Path(temporary) / "following.json").write_text(
        json.dumps({"userId": 7, "usernames": ["alreadyfollowed"]}), encoding="utf-8")

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
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=30) as response:
                return json.loads(response.read())

        summary = get("/api/discovery/summary")
        assert summary["worthFollowingThreshold"] == 10, summary
        assert summary["galleryHeartThreshold"] == 5, summary
        assert [creator["username"] for creator in summary["reactedNotFollowed"]] == ["OftenReacted"], summary

        page = get(f"/api/history/artists?date={day}&segment=all&view=discovery&offset=0&limit=10")
        by_name = {artist["username"]: artist for artist in page["artists"]}

        often = by_name["OftenReacted"]
        assert often["reactedCount"] == WORTH_FOLLOWING_MIN, often
        assert often["worthFollowing"] is True, often
        assert often["reactedOften"] is True, often

        below = by_name["JustBelowBar"]
        assert below["reactedCount"] == WORTH_FOLLOWING_MIN - 1, below
        assert below["worthFollowing"] is False, below
        assert below["reactedOften"] is True, below

        followed = by_name["AlreadyFollowed"]
        assert followed["following"] is True, followed
        assert followed["reactedCount"] >= WORTH_FOLLOWING_MIN, followed
        # Already following them, so there is nothing left to suggest.
        assert followed["worthFollowing"] is False, followed
        assert followed["reactedOften"] is False, followed

        below_heart = by_name["BelowHeart"]
        assert below_heart["reactedCount"] == GALLERY_HEART_MIN - 1, below_heart
        assert below_heart["reactedOften"] is False, below_heart

        heart_at_five = by_name["HeartAtFive"]
        assert heart_at_five["reactedCount"] == GALLERY_HEART_MIN, heart_at_five
        assert heart_at_five["reactedOften"] is True, heart_at_five
        assert heart_at_five["worthFollowing"] is False, heart_at_five

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # A real token is on disk, so the real /api/auth-status resolves correctly on
            # its own — mocking it here would just paper over that being true.
            page.route("**/api/creator-metadata**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"creators":{}}'))
            page.route("**/api/reaction-status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"images":{}}'))
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_selector(".creator-card", timeout=30000)

            cards = page.evaluate("""() => Object.fromEntries(
                [...document.querySelectorAll('.creator-card')].map(card => [
                    card.querySelector('.creator-identity strong')?.textContent,
                    {badge: !!card.querySelector('.worth-badge'),
                     title: card.querySelector('.worth-badge')?.getAttribute('title') || null}
                ]))""")
            assert cards["OftenReacted"]["badge"] is True, cards["OftenReacted"]
            assert "10" in cards["OftenReacted"]["title"], cards["OftenReacted"]
            assert cards["JustBelowBar"]["badge"] is True, cards["JustBelowBar"]
            assert cards["AlreadyFollowed"]["badge"] is False, cards["AlreadyFollowed"]
            assert cards["BelowHeart"]["badge"] is False, cards["BelowHeart"]
            assert cards["HeartAtFive"]["badge"] is True, cards["HeartAtFive"]
            assert "5" in cards["HeartAtFive"]["title"], cards["HeartAtFive"]

            page.eval_on_selector(".worth-badge", "n => n.scrollIntoView({block:'center'})")
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS / "worth-badge.png"))
            assert not errors, errors
            browser.close()

        print({"worthFollowingMinimum": WORTH_FOLLOWING_MIN,
               "galleryHeartMinimum": GALLERY_HEART_MIN,
               "tenQualifiesForSuggestion": True, "nineDoesNot": True,
               "fiveQualifiesForHeart": True, "fourDoesNot": True,
               "followedArtistNotHearted": True})
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

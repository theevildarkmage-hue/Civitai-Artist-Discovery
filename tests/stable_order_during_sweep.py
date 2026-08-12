"""'For you' must page through one consistent ordering, even while a background sweep
is still adding tag data underneath it.

Reported directly: the same creator's card appeared twice while scrolling. Found the
mechanism — day_view_order recomputes the personalised order from scratch on every
single page request, reading whatever tag data the background sweep has read so far.
Scrolling is normal while that sweep runs. If a creator's tags get read between two page
fetches, their score jumps and their rank moves, and whoever was sitting at the page
boundary at that moment gets served twice: once under the old order, once under the new.

This reproduces the mechanism directly against the real endpoint, not just the ordering
function in isolation: seed a creator with no tags known yet (so they rank last), fetch
a page, then have their tags "arrive" — as the sweep would — and fetch the next page,
and assert the two pages together contain no repeats.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8904

with tempfile.TemporaryDirectory(prefix="civitai-stable-order-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")

    # A small day: enough creators either side of a page boundary to make an ordering
    # shift observable, plus one "LateBloomer" whose tags have not been read yet.
    items = [{"id": 9950 + n, "postId": 9950 + n, "username": f"Creator{n}",
              "createdAt": f"{day}T1{n % 9}:00:00Z", "url": pixel, "width": 8, "height": 8,
              "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}
             for n in range(12)]
    items.append({"id": 9999, "postId": 9999, "username": "LateBloomer",
                  "createdAt": f"{day}T09:00:00Z", "url": pixel, "width": 8, "height": 8,
                  "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}})
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    store = TasteStore(Path(temporary) / "discovery")
    with store.connect() as db:
        # Two weighted taste tags. Every ordinary creator's cover matches one; once
        # swept, LateBloomer's matches both — summed, that clearly outscores everyone
        # else rather than merely tying them, so the shift this test needs is
        # unambiguous rather than resting on a tiebreak.
        db.executemany("INSERT INTO reacted_images(image_id, first_observed_at, last_observed_at) "
                       "VALUES(?,?,?)", [(n, "now", "now") for n in (1, 2, 3, 4, 5, 6)])
        db.executemany("INSERT INTO reacted_tags(image_id, tag_id, tag_name) VALUES(?,?,?)",
                       [(n, 1, "common-tag") for n in (1, 2, 3)]
                       + [(n, 2, "rare-tag") for n in (4, 5, 6)])
        # Every ordinary creator's cover already carries the common tag — read, real
        # scores, a stable order among themselves.
        db.executemany("INSERT INTO archive_image_tags(image_id, tag_name) VALUES(?,?)",
                       [(9950 + n, "common-tag") for n in range(12)])
        db.executemany("INSERT INTO archive_image_seen(image_id, fetched_at) VALUES(?,?)",
                       [(9950 + n, "now") for n in range(12)])
        # LateBloomer's tags are deliberately not recorded yet — exactly a creator the
        # sweep has not reached.

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

        # One continuous scroll session, exactly as the client generates: a fixed id
        # for this page load, paired with the token that changes when a day/view is
        # freshly opened (mirrors PAGE_SESSION + galleryToken in app.js).
        session = "test-session-abc-0"

        # Page 1, while LateBloomer's tags are still unread — ranks last, so this page
        # is exactly the ordinary, tag-matched creators.
        page1 = get(f"/api/history/artists?date={day}&segment=all&view=foryou&offset=0&limit=6&session={session}")
        page1_names = [a["username"] for a in page1["artists"]]
        assert "LateBloomer" not in page1_names, page1_names

        # The sweep "reaches" LateBloomer mid-session: their cover turns out to match
        # both weighted tags, so a live recomputation would now rank them above every
        # single-tag creator, not just tie with one.
        with store.connect() as db:
            db.executemany("INSERT INTO archive_image_tags(image_id, tag_name) VALUES(?,?)",
                           [(9999, "common-tag"), (9999, "rare-tag")])
            db.execute("INSERT INTO archive_image_seen(image_id, fetched_at) VALUES(9999,'now')")

        # Without freezing, page 2 would now be computed against a shifted order and
        # would repeat whoever page 1's last slot handed off to. With freezing — same
        # session id — page 2 continues the exact same order page 1 saw.
        page2 = get(f"/api/history/artists?date={day}&segment=all&view=foryou&offset=6&limit=6&session={session}")
        page2_names = [a["username"] for a in page2["artists"]]

        overlap = set(page1_names) & set(page2_names)
        assert not overlap, (page1_names, page2_names, "the same creator was served on both pages")
        assert "LateBloomer" not in page2_names, \
            "the frozen session should not see data that arrived after it started"

        # A genuinely fresh session — a new day/view load, not a paginated continuation
        # of the one above — must still pick up what the sweep added.
        reopened = "test-session-xyz-0"
        fresh = get(f"/api/history/artists?date={day}&segment=all&view=foryou&offset=0&limit=13&session={reopened}")
        fresh_names = [a["username"] for a in fresh["artists"]]
        assert "LateBloomer" in fresh_names, fresh_names

        # A request that sends no session id at all (an older client, or any other
        # caller) must never be told it can rely on a frozen order — always fresh.
        no_session = get(f"/api/history/artists?date={day}&segment=all&view=foryou&offset=0&limit=13")
        assert "LateBloomer" in [a["username"] for a in no_session["artists"]]
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

print({"pageBoundaryNeverRepeatsWhileSweepRuns": True, "freshLoadPicksUpSweptData": True})

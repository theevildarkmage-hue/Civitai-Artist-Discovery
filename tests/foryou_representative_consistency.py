"""'For you' must score whichever image the card actually shows.

Found while investigating why a card with no visible match still ranked above cards that
had one: the archive picks a creator's cover pseudo-randomly, and separately, a cover the
account hides gets swapped for a visible image at display time. Scoring used the raw
archive pick; the explanation used the swapped, displayed one. When those two images
differ, a creator's real match can be sitting on a hidden image nobody ever scores, while
the shown card silently scores as zero.

This reproduces it directly: give a creator two images, hide whichever one the archive's
own hash would pick as the representative, and put a real taste match on the other.
"""

import hashlib
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
PORT = 8899


def representative_index(day: str, username_key: str, count: int) -> int:
    """Mirror HistoryArchive.build_artist_index's pseudo-random cover pick exactly."""
    digest = hashlib.sha256(f"{day}:{username_key}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % count


with tempfile.TemporaryDirectory(prefix="civitai-foryou-hidden-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    history = HistoryArchive(Path(temporary) / "history")

    # Newer first, matching the DESC query build_artist_index reads its items from.
    newer_id, older_id = 9601, 9602
    key = "hiddenmatch"
    idx = representative_index(day, key, 2)
    hidden_id, visible_id = (newer_id, older_id) if idx == 0 else (older_id, newer_id)

    def image(image_id, minute):
        return {"id": image_id, "postId": image_id, "username": "HiddenMatch",
                "createdAt": f"{day}T13:{minute:02d}:00Z", "url": pixel, "width": 8, "height": 8,
                "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}

    history._upsert_normalized([
        image(newer_id, 30), image(older_id, 20),
        # A plain, unhidden control creator: no match, must stay ranked behind HiddenMatch.
        {"id": 9603, "postId": 9603, "username": "PlainNoMatch", "createdAt": f"{day}T12:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "stats": {"reactionCount": 1}},
    ], forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    with history.connect() as db:
        stored = db.execute("SELECT representative_id FROM day_artists WHERE day=? AND username_key=?",
                            (day, key)).fetchone()[0]
    assert stored == hidden_id, (stored, hidden_id, "test's hash replica drifted from production")

    store = TasteStore(Path(temporary) / "discovery")
    # A weighted taste tag: >=3 reacted images carrying it, no baseline needed for a
    # clean, predictable weight (lift defaults to 1.0 with no baseline sampled).
    with store.connect() as db:
        db.executemany("INSERT INTO reacted_images(image_id, first_observed_at, last_observed_at) "
                       "VALUES(?,?,?)", [(n, "now", "now") for n in (1, 2, 3)])
        db.executemany("INSERT INTO reacted_tags(image_id, tag_id, tag_name) VALUES(?,?,?)",
                       [(n, 1, "distinctive-tag") for n in (1, 2, 3)])
        # The visible image carries the real match; the hidden one carries nothing that
        # scores, so the old (buggy) behaviour of scoring the raw archive pick would give
        # this creator no score at all.
        db.execute("INSERT INTO archive_image_tags(image_id, tag_name) VALUES(?,?)",
                   (visible_id, "distinctive-tag"))
        db.execute("INSERT INTO hidden_images(image_id) VALUES(?)", (hidden_id,))

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

        page = get(f"/api/history/artists?date={day}&segment=all&view=foryou&offset=0&limit=10")
        by_name = {artist["username"]: artist for artist in page["artists"]}

        matched = by_name["HiddenMatch"]
        control = by_name["PlainNoMatch"]

        # The card must open on the visible image, never the hidden one.
        assert matched["representative"]["id"] == visible_id, matched["representative"]

        # The score that earned its rank and the explanation shown must talk about the
        # same image: a real match must produce a real explanation.
        assert matched["matchedTags"] == ["distinctive-tag"], matched["matchedTags"]

        # And that real match must actually outrank a creator with no match at all —
        # the exact symptom that exposed the bug (a no-match card ranking first).
        order = [artist["username"] for artist in page["artists"]]
        assert order.index("HiddenMatch") < order.index("PlainNoMatch"), order
        assert control["matchedTags"] == [], control["matchedTags"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

print({"scoresDisplayedImageNotHiddenOne": True, "explanationMatchesTheScore": True,
       "realMatchOutranksNoMatch": True})

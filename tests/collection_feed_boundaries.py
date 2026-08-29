"""Daily blocks publish only after every browsing-level feed crosses the boundary."""

from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import (COLLECTION_VERSION, FEED_FLOOR_PROBE_OFFSET,
                               HistoryArchive)


DAY = "2026-07-31"
START = f"{DAY}T05:00:00Z"
END = f"{DAY}T17:00:00Z"


def image(image_id, created, level=1):
    names = {1: "None", 2: "Soft", 4: "Mature", 8: "X", 16: "X"}
    return {"id": image_id, "postId": image_id, "username": f"artist-{image_id}",
            "createdAt": created, "url": f"https://example.invalid/{image_id}.jpg",
            "type": "image", "nsfwLevel": names[level], "browsingLevel": level,
            "stats": {"reactionCount": 1}}


def wait(archive, key):
    deadline = time.monotonic() + 5
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    return archive.status(key)


ANCIENT = ({"items": [{"id": 0, "createdAt": "2020-01-01T00:00:00Z"}]}, 60)


def is_floor_probe(params) -> bool:
    """The collector asks each feed how far back it reaches before collecting anything."""
    return str((params or {}).get("cursor", "")).startswith(f"{FEED_FLOOR_PROBE_OFFSET}|")


with tempfile.TemporaryDirectory(prefix="civitai-feed-ceiling-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    archive._seek_cursor = lambda *a, **k: (None, 0, 0)
    archive._request = lambda params=None, *a, **k: ANCIENT if is_floor_probe(params) else ({
        "items": [image(1, f"{DAY}T16:00:00Z")], "metadata": {"nextCursor": None}}, 100)
    archive.start(DAY, START, END, "America/Chicago", "morning", "Soft")
    status = wait(archive, f"{DAY}#morning")
    assert status["state"] == "error" and status["errorKind"] == "history_window" \
        and not status["complete"], status
    assert status["itemCount"] == 1, "progress should remain resumable"
    with archive.connect() as db:
        assert not db.execute("SELECT complete FROM days WHERE day=?",
                              (f"{DAY}#morning",)).fetchone()[0]
        assert not db.execute("SELECT complete FROM block_feeds WHERE block_key=?",
                              (f"{DAY}#morning",)).fetchone()[0]


with tempfile.TemporaryDirectory(prefix="civitai-feed-shards-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    archive._seek_cursor = lambda *a, **k: (None, 0, 0)
    masks = []

    def request(params, **_kwargs):
        if is_floor_probe(params):
            return ANCIENT
        mask = int(params["browsingLevel"])
        masks.append(mask)
        level = {1: 1, 2: 2, 4: 4, 8: 8, 16: 16}[mask]
        return ({"items": [image(mask, f"{DAY}T12:00:00Z", level),
                            image(mask + 100, f"{DAY}T04:59:59Z", level)],
                 "metadata": {"nextCursor": "older"}}, 100)

    archive._request = request
    archive.start(DAY, START, END, "America/Chicago", "morning", "X")
    status = wait(archive, f"{DAY}#morning")
    assert status["complete"], status
    assert masks == [1, 2, 4, 8, 16], masks
    with archive.connect() as db:
        feeds = [tuple(row) for row in db.execute(
            "SELECT browsing_mask,complete FROM block_feeds WHERE block_key=? ORDER BY browsing_mask",
            (f"{DAY}#morning",))]
        assert feeds == [(1, 1), (2, 1), (4, 1), (8, 1), (16, 1)], feeds
        assert db.execute("SELECT collection_version FROM days WHERE day=?",
                          (f"{DAY}#morning",)).fetchone()[0] == COLLECTION_VERSION


with tempfile.TemporaryDirectory(prefix="civitai-legacy-coverage-") as temporary:
    root = Path(temporary) / "history"
    archive = HistoryArchive(root)
    archive._upsert_normalized([image(900, f"{DAY}T16:00:00Z")], forced_date=DAY)
    with archive.connect() as db:
        db.execute("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,900)",
                   (f"{DAY}#morning",))
        db.execute("""INSERT INTO days(day,complete,start_utc,end_utc,content_rating,
                    collection_version,updated_at) VALUES(?,1,?,?,'Soft',0,?)""",
                   (f"{DAY}#morning", START, END, "2026-08-01T00:00:00Z"))
        db.execute("INSERT INTO days(day,complete,content_rating,updated_at) VALUES(?,1,'Soft',?)",
                   (DAY, "2026-08-01T00:00:00Z"))
        # Completed format-3 halves used one combined safe feed. They remain viewable;
        # only unfinished jobs migrate to the format-4 independent cursors.
        previous_key = f"{DAY}#evening"
        db.execute("""INSERT INTO days(day,complete,start_utc,end_utc,content_rating,
                    collection_version,updated_at) VALUES(?,1,?,?,'Soft',3,?)""",
                   (previous_key, START, END, "2026-08-01T00:00:00Z"))
        db.execute("""INSERT INTO block_feeds(block_key,browsing_mask,complete,updated_at)
                    VALUES(?,3,1,?)""", (previous_key, "2026-08-01T00:00:00Z"))
    reopened = HistoryArchive(root)
    assert not reopened.status(f"{DAY}#morning")["archiveComplete"]
    assert not reopened.status(DAY)["archiveComplete"]
    assert reopened.status(previous_key)["archiveComplete"]


print({"cursorCeilingRejected": True, "progressPreserved": True,
       "feeds": [1, 2, 4, 8, 16], "legacyTruncationReopened": True})

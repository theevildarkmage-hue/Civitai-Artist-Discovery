"""Both halves complete publishes an all-day archive, locally and without network."""

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive

DAY = "2026-07-31"


def image(image_id, created, username):
    return {"id": image_id, "postId": image_id, "username": username, "createdAt": created,
            "url": f"https://image.civitai.com/test/{image_id}.jpeg", "width": 768, "height": 1024,
            "type": "image", "nsfwLevel": "Soft", "stats": {"reactionCount": image_id}}


def finish(archive, key):
    deadline = time.monotonic() + 5
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert archive.status(key)["complete"], archive.status(key)


with tempfile.TemporaryDirectory(prefix="civitai-merged-day-", ignore_cleanup_errors=True) as temporary:
    archive = HistoryArchive(Path(temporary) / "history")

    responses = [image(1, f"{DAY}T08:00:00Z", "MorningArtist"), image(9, f"{DAY}T04:59:59Z", "Older")]
    archive._request = lambda *a, **k: ({"items": responses, "metadata": {"nextCursor": "older"}}, 100)
    archive.start(DAY, f"{DAY}T05:00:00Z", f"{DAY}T17:00:00Z", "America/Chicago", "morning")
    finish(archive, f"{DAY}#morning")

    # One half complete is not a day: nothing may be published yet.
    assert archive.merge_completed_halves(f"{DAY}#morning") is None
    assert not archive.status(DAY)["complete"], "a half day was published as a full day"

    responses = [image(2, f"{DAY}T20:00:00Z", "EveningArtist"),
                 image(3, f"{DAY}T21:00:00Z", "MorningArtist"),
                 image(8, f"{DAY}T16:59:59Z", "Older")]
    archive.start(DAY, f"{DAY}T17:00:00Z", "2026-08-01T05:00:00Z", "America/Chicago", "evening")
    finish(archive, f"{DAY}#evening")

    # Completing the second half publishes the union automatically.
    status = archive.status(DAY)
    assert status["complete"], status
    summary = archive.day_summary(DAY)
    assert summary["imageCount"] == 3, summary
    assert summary["artistCount"] == 2, summary

    page = archive.artists_page(DAY, 0, 10)
    names = {item["username"]: item["imageCount"] for item in page}
    assert names == {"MorningArtist": 2, "EveningArtist": 1}, names

    # The halves are untouched and still independently browsable.
    assert archive.day_summary(f"{DAY}#morning")["imageCount"] == 1
    assert archive.day_summary(f"{DAY}#evening")["imageCount"] == 2
    # MorningArtist posted in both halves, so the merged day credits them with two images
    # while each half still shows only its own.
    assert {x["username"]: x["imageCount"] for x in archive.artists_page(f"{DAY}#evening", 0, 10)} \
        == {"EveningArtist": 1, "MorningArtist": 1}
    assert {x["username"]: x["imageCount"] for x in archive.artists_page(f"{DAY}#morning", 0, 10)} \
        == {"MorningArtist": 1}

    # Image rows stay deduplicated; only membership is added.
    with archive.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM images WHERE local_date=?", (DAY,)).fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM block_images WHERE block_key=?", (DAY,)).fetchone()[0] == 3

    # Re-running is harmless.
    assert archive.merge_completed_halves(f"{DAY}#evening") == DAY
    assert archive.day_summary(DAY)["imageCount"] == 3

    # Days whose halves finished before this feature existed are published on startup.
    with archive.connect() as db:
        db.execute("DELETE FROM days WHERE day=?", (DAY,))
        db.execute("DELETE FROM day_artists WHERE day=?", (DAY,))
    assert not archive.status(DAY)["complete"]
    reopened = HistoryArchive(Path(temporary) / "history")
    assert reopened.status(DAY)["complete"], "an existing pair of halves was not backfilled"
    assert reopened.day_summary(DAY)["artistCount"] == 2
    # A day with only one half stays unpublished across restarts.
    with reopened.connect() as db:
        db.execute("DELETE FROM days WHERE day=?", (f"{DAY}#morning",))
        db.execute("DELETE FROM days WHERE day=?", (DAY,))
    assert not HistoryArchive(Path(temporary) / "history").status(DAY)["complete"]

print({"halfDayNotPublished": True, "mergedOnSecondHalf": True, "mergedImages": 3,
       "mergedArtists": 2, "halvesUnchanged": True, "idempotent": True, "backfilledOnStartup": True})

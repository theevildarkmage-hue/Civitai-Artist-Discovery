"""Morning and evening archives build independently while sharing image rows."""

from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive


DAY = "2026-07-31"


def image(image_id, created):
    return {"id": image_id, "postId": image_id, "username": "block-test", "createdAt": created,
        "url": f"https://image.civitai.com/test/{image_id}.jpeg", "width": 768, "height": 1024,
        "type": "image", "nsfwLevel": "Soft", "stats": {"reactionCount": 1}}


def finish(archive, key):
    deadline = time.monotonic() + 5
    while archive.status(key)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert archive.status(key)["complete"]


with tempfile.TemporaryDirectory(prefix="civitai-half-days-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    responses = [image(1, "2026-07-31T12:00:00Z"), image(99, "2026-07-31T04:59:59Z")]
    archive._request = lambda *args, **kwargs: ({"items": responses, "metadata": {"nextCursor": "older"}}, 100)
    archive.start(DAY, "2026-07-31T05:00:00Z", "2026-07-31T17:00:00Z", "America/Chicago", "morning")
    finish(archive, f"{DAY}#morning")

    responses = [image(2, "2026-07-31T20:00:00Z"), image(98, "2026-07-31T16:59:59Z")]
    archive.start(DAY, "2026-07-31T17:00:00Z", "2026-08-01T05:00:00Z", "America/Chicago", "evening")
    finish(archive, f"{DAY}#evening")

    assert archive.day_summary(f"{DAY}#morning")["imageCount"] == 1
    assert archive.day_summary(f"{DAY}#evening")["imageCount"] == 1
    assert [x["id"] for x in archive.artist_images(f"{DAY}#morning", "block-test")] == [1]
    assert [x["id"] for x in archive.artist_images(f"{DAY}#evening", "block-test")] == [2]
    assert archive.day_summary(DAY)["imageCount"] == 2
    with archive.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 2

print({"morningImages": 1, "eveningImages": 1, "deduplicatedRows": 2, "legacyMembership": 2})

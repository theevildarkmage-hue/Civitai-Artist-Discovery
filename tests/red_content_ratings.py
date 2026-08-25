"""Red collection stays metadata-light and rating changes are coverage-aware."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import HistoryArchive
from discovery.site import API_URL, SITE_ORIGIN

DAY = "2026-08-09"


def item(image_id, rating, browsing_level=None, created="2026-08-09T18:00:00Z"):
    return {"id": image_id, "postId": image_id, "username": f"Artist{image_id}",
            "createdAt": created, "url": f"https://image.civitai.com/test/{image_id}.jpeg",
            "type": "image", "nsfwLevel": rating, "browsingLevel": browsing_level,
            "stats": {}}


with tempfile.TemporaryDirectory() as temporary:
    archive = HistoryArchive(Path(temporary) / "history", "Soft")
    captured = []

    def request(params, **_):
        captured.append(dict(params))
        return ({"items": [item(1, "None"), item(2, "Soft"),
                            item(99, "None", created="2026-08-09T11:59:59Z")],
                 "metadata": {}}, 100)

    archive._request = request
    start = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    archive.start(DAY, start.isoformat(), end.isoformat(), "UTC", requested_content_rating="Soft")
    deadline = time.monotonic() + 5
    while archive.status(DAY)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    assert API_URL == f"{SITE_ORIGIN}/api/v1/images"
    assert captured and all(call["withMeta"] == "false" for call in captured)
    assert all(call["browsingLevel"] == 3 for call in captured)
    assert all("nsfw" not in call for call in captured)
    assert archive.status(DAY)["archiveContentRating"] == "Soft"
    archive.set_content_rating("Mature")
    status = archive.status(DAY)
    assert status["needsUpgrade"] and not status["complete"]

    archive._upsert_normalized([item(3, "Mature")], forced_date=DAY)
    request_count = len(captured)
    archive.set_content_rating("Soft")
    assert len(captured) == request_count, "lowering content coverage made an API request"
    assert archive.status(DAY)["complete"], "lowering hid a completed local archive"
    assert archive.day_summary(DAY)["imageCount"] == 2
    assert archive.artist_images(DAY, "Artist3") == []

    archive._upsert_normalized([item(4, "X", 8), item(5, "X", 16)], forced_date=DAY)
    archive.set_content_filter([4])
    assert archive.day_summary(DAY)["imageCount"] == 1
    assert [row["id"] for row in archive.artist_images(DAY, "Artist3")] == [3]
    archive.set_content_filter([16])
    assert archive.day_summary(DAY)["imageCount"] == 1
    assert archive.artist_images(DAY, "Artist4") == []
    assert [row["id"] for row in archive.artist_images(DAY, "Artist5")] == [5]
    archive.set_content_filter([1, 2])

    # A partial cursor is tied to the exact browsing-level feed that issued it.
    partial = "2026-08-08"
    with archive.connect() as db:
        db.execute("""INSERT INTO days(day,complete,content_rating,collection_version,updated_at)
                      VALUES(?,0,?,2,?)""", (partial, "X", datetime.now(timezone.utc).isoformat()))
        db.executemany("""INSERT INTO block_feeds(block_key,browsing_mask,complete,scan_cursor,updated_at)
                          VALUES(?,?,0,?,?)""",
            [(partial, mask, "saved-cursor" if mask == 3 else None,
              datetime.now(timezone.utc).isoformat()) for mask in (3, 4, 8, 16)])
    captured.clear()
    archive._request = request
    archive.start(partial, "2026-08-08T00:00:00+00:00", "2026-08-09T00:00:00+00:00", "UTC",
                  requested_content_rating="X")
    deadline = time.monotonic() + 5
    while archive.status(partial)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    assert captured and captured[0]["cursor"] == "saved-cursor", captured
    assert captured[0]["browsingLevel"] == 3, captured[0]

print({"redApi": API_URL, "metadataLight": True, "safeDefault": True,
       "higherCoverageRequiresUpgrade": True, "loweringHidesMature": True,
       "loweringUsesSavedData": True, "partialCursorKeepsOriginalFeed": True,
       "individualRAndExplicitLevels": True})

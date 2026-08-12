"""Red collection stays metadata-light and rating changes are coverage-aware."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import HistoryArchive
from discovery.site import API_URL, SITE_ORIGIN

DAY = "2026-08-09"


def item(image_id, rating):
    return {"id": image_id, "postId": image_id, "username": f"Artist{image_id}",
            "createdAt": "2026-08-09T18:00:00Z", "url": f"https://image.civitai.com/test/{image_id}.jpeg",
            "type": "image", "nsfwLevel": rating, "stats": {}}


with tempfile.TemporaryDirectory() as temporary:
    archive = HistoryArchive(Path(temporary) / "history", "Soft")
    captured = []

    def request(params, **_):
        captured.append(dict(params))
        return ({"items": [item(1, "None"), item(2, "Soft")], "metadata": {}}, 100)

    archive._request = request
    start = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    archive.jobs[DAY] = {"state": "loading", "startedMonotonic": 0}
    archive.cancel_events[DAY] = threading.Event()
    archive._collect(DAY, DAY, start, end)
    assert API_URL == f"{SITE_ORIGIN}/api/v1/images"
    assert captured and all(call["withMeta"] == "false" for call in captured)
    assert all(call["nsfw"] == "Soft" for call in captured)
    assert archive.status(DAY)["archiveContentRating"] == "Soft"
    archive.set_content_rating("Mature")
    status = archive.status(DAY)
    assert status["needsUpgrade"] and not status["complete"]

    archive._upsert_normalized([item(3, "Mature")], forced_date=DAY)
    archive.set_content_rating("Soft")
    assert archive.day_summary(DAY)["imageCount"] == 2
    assert archive.artist_images(DAY, "Artist3") == []

print({"redApi": API_URL, "metadataLight": True, "safeDefault": True,
       "higherCoverageRequiresUpgrade": True, "loweringHidesMature": True})

"""Popular totals every reaction for the day and opens on the strongest image."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import HistoryArchive


DAY = "2026-08-14"
PIXEL = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E"


def image(image_id, username, minute, reactions):
    return {"id": image_id, "postId": image_id, "username": username,
            "createdAt": f"{DAY}T12:{minute:02d}:00+00:00", "url": PIXEL,
            "type": "image", "nsfwLevel": "None", "browsingLevel": 1,
            "stats": {"reactionCount": reactions}}


with tempfile.TemporaryDirectory(prefix="popular-ranking-") as temporary:
    history = HistoryArchive(Path(temporary) / "history")
    history._upsert_normalized([
        image(1, "DailyTotal", 10, 60), image(2, "DailyTotal", 20, 60),
        image(3, "SingleHit", 30, 100),
        image(4, "NewestTie", 40, 120),
    ], forced_date=DAY)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (DAY, datetime.now(timezone.utc).isoformat()))
    history.build_artist_index(DAY)

    keys = history.day_artist_keys(DAY)
    assert [row["username"] for row in keys] == ["NewestTie", "DailyTotal", "SingleHit"], keys
    total = next(row for row in keys if row["username"] == "DailyTotal")
    assert total["representativeId"] == 2, total  # equal reactions: newest wins
    page = history.artists_page(DAY, 0, 10)
    assert [artist["username"] for artist in page] == ["NewestTie", "DailyTotal", "SingleHit"], page
    assert page[1]["representative"]["id"] == 2, page[1]

print({"ranksByDailyReactionTotal": True, "mostReactedImageFirst": True,
       "newestBreaksReactionTies": True})

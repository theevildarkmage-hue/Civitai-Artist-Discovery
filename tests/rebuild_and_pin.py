"""Safe day rebuilds and connected-creator ordering."""

from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive


DAY = "2026-07-31"
START = "2026-07-31T05:00:00Z"
END = "2026-08-01T05:00:00Z"


def image(image_id, username, rating="None", created="2026-07-31T16:00:00Z", reactions=0):
    return {"id": image_id, "postId": image_id, "username": username, "createdAt": created,
        "url": f"https://image.civitai.com/test/{image_id}.jpeg", "width": 768, "height": 1024,
        "type": "image", "nsfwLevel": rating, "stats": {"reactionCount": reactions}}


def wait_done(archive):
    deadline = time.monotonic() + 5
    while archive.status(DAY)["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert time.monotonic() < deadline


with tempfile.TemporaryDirectory(prefix="civitai-rebuild-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    archive._upsert_normalized([image(1, "owner"), image(10, "popular", reactions=999)], forced_date=DAY)
    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete,top_cursor,updated_at) VALUES(?,1,?,?)", (DAY, "saved-day-start", "now"))
    archive.build_artist_index(DAY)

    captured = []
    def completed_request(params, minimum_interval=1.0, on_delay=None):
        captured.append(dict(params))
        return {"items": [image(2, "owner", "Soft"),
            image(3, "older", created="2026-07-31T04:59:59Z")], "metadata": {"nextCursor": "older"}}, 100
    archive._request = completed_request
    state = archive.rebuild(DAY, START, END, "America/Chicago")
    assert state["archiveComplete"] and state["rebuilding"]
    wait_done(archive)
    assert captured[0]["cursor"] == "saved-day-start"
    assert [row["id"] for row in archive.artist_images(DAY, "owner")] == [2, 1]
    assert archive.artists_page(DAY, 0, 1, "owner")[0]["username"] == "owner"
    assert archive.artists_page(DAY, 1, 1, "owner")[0]["username"] != "owner"

    entered = threading.Event(); release = threading.Event()
    before = archive.day_summary(DAY)
    def blocked_request(params, minimum_interval=1.0, on_delay=None):
        entered.set(); release.wait(2)
        return {"items": [image(4, "cancelled-addition", "Soft")], "metadata": {"nextCursor": None}}, 100
    archive._request = blocked_request
    archive.rebuild(DAY, START, END, "America/Chicago")
    assert entered.wait(2)
    archive.cancel(DAY); release.set(); wait_done(archive)
    after = archive.day_summary(DAY)
    assert archive.status(DAY)["complete"] and archive.status(DAY)["state"] == "cancelled"
    assert (before["imageCount"], before["artistCount"]) == (after["imageCount"], after["artistCount"])
    assert not archive.artist_images(DAY, "cancelled-addition")

print({"savedCursorUsed": True, "cancelPreservedGallery": True, "connectedCreatorFirst": True})

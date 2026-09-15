"""Videos are archived with images and served with a still frame plus a playback file."""

from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import FEED_FLOOR_PROBE_OFFSET, HistoryArchive


DAY = "2026-07-31"
START = f"{DAY}T05:00:00Z"
END = f"{DAY}T17:00:00Z"
CDN = "https://image.civitai.com/key/uuid"


def item(item_id, kind, username="artist", reactions=1, level=1, created=f"{DAY}T12:00:00Z"):
    extension = "mp4" if kind == "video" else "jpeg"
    names = {1: "None", 2: "Soft", 4: "Mature", 8: "X", 16: "X"}
    return {"id": item_id, "postId": item_id, "username": username, "createdAt": created,
            "url": f"{CDN}/original=true/{item_id}.{extension}", "type": kind,
            "nsfwLevel": names[level], "browsingLevel": level,
            "stats": {"reactionCount": reactions}}


# The collector keeps the videos the Newest feed already interleaves with images.
with tempfile.TemporaryDirectory(prefix="video-collect-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    archive._seek_cursor = lambda *a, **k: (None, 0, 0)

    def request(params=None, **_kwargs):
        if str((params or {}).get("cursor", "")).startswith(f"{FEED_FLOOR_PROBE_OFFSET}|"):
            return {"items": [{"id": 0, "createdAt": "2020-01-01T00:00:00Z"}]}, 60
        return ({"items": [item(1, "image"), item(2, "video"), item(3, "model"),
                           item(4, "image", created=f"{DAY}T04:59:59Z")],
                 "metadata": {"nextCursor": "older"}}, 100)

    archive._request = request
    archive.start(DAY, START, END, "America/Chicago", "morning", "Soft")
    deadline = time.monotonic() + 5
    while archive.status(f"{DAY}#morning")["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(.01)
    assert archive.status(f"{DAY}#morning")["complete"]
    with archive.connect() as db:
        kept = dict(db.execute("SELECT id,type FROM images ORDER BY id").fetchall())
    assert kept == {1: "image", 2: "video"}, kept


with tempfile.TemporaryDirectory(prefix="video-covers-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history", selected_browsing_levels=[1, 2])
    archive._upsert_normalized([
        item(10, "image", "Mixed", reactions=90), item(11, "video", "Mixed", reactions=5),
        item(12, "video", "Mixed", reactions=40),
        item(20, "image", "ImagesOnly", reactions=50),
        item(30, "video", "HiddenVideo", reactions=9),
        item(40, "video", "AboveFilter", reactions=9, level=8),
    ], forced_date=DAY)

    # A video card shows a JPEG still (an <img> cannot show an MP4) and carries the
    # smaller playback file separately; images keep their existing preview URL.
    with archive.connect() as db:
        video = archive._row_item(db.execute("SELECT * FROM images WHERE id=12").fetchone(), details=True)
        still = archive._row_item(db.execute("SELECT * FROM images WHERE id=10").fetchone())
    assert video["thumbnailUrl"] == f"{CDN}/anim=false,width=768/12.mp4", video
    assert video["videoUrl"] == f"{CDN}/transcode=true,width=450,optimized=true/12.mp4", video
    assert video["detailImageUrl"] == f"{CDN}/anim=false,width=1280/12.mp4", video
    assert video["detailVideoUrl"] == f"{CDN}/transcode=true,width=768,optimized=true/12.mp4", video
    assert still["thumbnailUrl"] == f"{CDN}/width=768/10.jpeg" and "videoUrl" not in still, still

    # Each creator's most-reacted video, skipping hidden ones and levels not being viewed.
    covers = archive.creator_video_covers(DAY, excluded_images={30})
    assert covers == {"mixed": 12}, covers


print({"videosCollected": True, "stillFrameThumbnail": True, "playbackUrl": True,
       "videoCoversRespectFilters": True})

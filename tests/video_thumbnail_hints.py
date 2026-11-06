"""Listing hints are bounded, rating compatible, ephemeral, and never rewrite archives."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import io
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.thumbnails import ThumbnailHints, supported_thumbnail, thumbnail_size
from discovery.history import HistoryArchive
from discovery.timemachine import _representative

CDN = "https://image.civitai.com/key/video/original=true/video.mp4"
THUMB = "https://image.civitai.com/key/poster/anim=false,optimized=true/poster.jpeg"


def listing(mask=1, image_id=1, url=THUMB):
    return {"items": [{"id": image_id, "type": "video", "url": CDN,
                       "thumbnail": {"url": url}, "browsingLevel": mask}]}


now = [0]
hints = ThumbnailHints(max_entries=2, ttl=10, clock=lambda: now[0])
hints.capture(listing(), 3)
assert hints.get(1, CDN, [1]) is None
assert hints.get(1, CDN, [1, 2]) == THUMB.replace("/poster.jpeg", ",width=768/poster.jpeg")
assert hints.get(1, CDN + "changed", [1, 2]) is None
hints.capture(listing(image_id=2), 1)
hints.capture(listing(image_id=3), 1)
assert hints.get(1, CDN, [1, 2]) is None and len(hints.entries) == 2
now[0] = 10
assert hints.get(2, CDN, [1]) is None
tiny = ThumbnailHints(max_bytes=5)
tiny.capture(listing(), 1)
assert not tiny.entries
for bad in [None, 1, "", "http://image.civitai.com/x", "https://evil.invalid/x",
            "https://image.civitai.com.evil.invalid/x", "https://u:p@image.civitai.com/x",
            "https://image.civitai.com:8080/x", "https://image.civitai.com/x\nsecret"]:
    assert supported_thumbnail(bad) is None, bad
for opaque in [THUMB + "?signature=private", "https://image.civitai.com/opaque/photo.jpeg"]:
    assert thumbnail_size(opaque, 768) == opaque
hints.capture({"items": [{"id": 4, "type": "image", "url": CDN, "thumbnail": {"url": THUMB}}]}, 1)
assert hints.get(4, CDN, [1]) is None

with tempfile.TemporaryDirectory(prefix="cad3-video-hints-") as temporary:
    archive = HistoryArchive(Path(temporary))
    archive._upsert_normalized([{"id": 1, "username": "Artist", "createdAt": "2026-10-15T12:00:00Z",
                                "url": CDN, "type": "video", "browsingLevel": 1}])
    with archive.connect() as db:
        before = dict(db.execute("SELECT * FROM images WHERE id=1").fetchone())
        schema = list(db.execute("PRAGMA table_info(images)"))
        old = archive._row_item(db.execute("SELECT * FROM images WHERE id=1").fetchone())
    assert "thumbnailCandidates" not in old
    response = io.BytesIO(json.dumps(listing()).encode())
    response.headers = {}
    with patch("discovery.history.urllib.request.urlopen", return_value=response) as request:
        archive._request({"browsingLevel": 1}, minimum_interval=0)
    assert request.call_count == 1
    with archive.connect() as db:
        row = db.execute("SELECT * FROM images WHERE id=1").fetchone()
        assert dict(row) == before and list(db.execute("PRAGMA table_info(images)")) == schema
        decorated = archive._row_item(row, details=True)
    assert decorated["thumbnailCandidates"][-1] == old["thumbnailUrl"]
    assert "poster" in decorated["thumbnailUrl"] and "width=768" in decorated["thumbnailUrl"]
    tm = {**before, "image_id": 1}
    assert _representative(tm, archive)["thumbnailUrl"] == decorated["thumbnailUrl"]
    archive.visible_levels = (2,)
    assert archive.decorate_video_thumbnail(dict(old)) == old

print({"selection": True, "ratingContext": True, "boundedCache": True,
       "opaqueUrlsPreserved": True, "archiveUnchanged": True, "noExtraRequests": True})

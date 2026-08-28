"""Civitai listing hashes are retained and produce request-free duplicate metrics."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.civitai import normalize
from discovery.history import HistoryArchive


def image(image_id: int, username: str, day: str, visual_hash: str | None) -> dict:
    raw = {"id": image_id, "postId": image_id, "username": username,
           "createdAt": f"{day}T12:00:00Z", "url": f"https://example.invalid/{image_id}.png",
           "width": 1024, "height": 1209, "type": "image", "browsingLevel": 2,
           "stats": {}}
    if visual_hash is not None:
        raw["hash"] = visual_hash
    return normalize(raw)


with tempfile.TemporaryDirectory(prefix="civitai-visual-hashes-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    first = "2026-08-23"
    second = "2026-08-24"
    shared = "UcPPx|WB_N%Mn-oftRWB%Mxbt7WVxuRjWBt7"

    archive._upsert_normalized([
        image(1, "CreatorA", first, shared),
        image(2, "CreatorB", first, shared),
        image(3, "CreatorC", first, "different-hash"),
        image(4, "LegacyNoHash", first, None),
    ], forced_date=first)
    archive._upsert_normalized([image(5, "CreatorA", second, shared)], forced_date=second)

    with archive.connect() as db:
        assert db.execute("SELECT visual_hash FROM images WHERE id=1").fetchone()[0] == shared
        assert db.execute("SELECT visual_hash FROM images WHERE id=4").fetchone()[0] is None

    day = archive.duplicate_report(first)
    assert day == {"scope": first, "imageCount": 4, "hashedImages": 3,
                   "hashCoveragePercent": 75.0, "duplicateGroups": 1,
                   "duplicateUploads": 1, "crossCreatorGroups": 1,
                   "crossDayGroups": 0}, day

    overall = archive.duplicate_report()
    assert overall == {"scope": "all", "imageCount": 5, "hashedImages": 4,
                       "hashCoveragePercent": 80.0, "duplicateGroups": 1,
                       "duplicateUploads": 2, "crossCreatorGroups": 1,
                       "crossDayGroups": 1}, overall

    # A later listing without the field must not erase an already saved hash.
    archive._upsert_normalized([image(1, "CreatorA", first, None)], forced_date=first)
    with archive.connect() as db:
        assert db.execute("SELECT visual_hash FROM images WHERE id=1").fetchone()[0] == shared

print({"visualHashStored": True, "missingHashPreserved": True,
       "sameDayMetrics": True, "crossDayMetrics": True})

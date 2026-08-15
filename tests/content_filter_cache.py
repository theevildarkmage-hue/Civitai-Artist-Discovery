"""Content switches are lazy and reuse indexes already built for a level combination."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import HistoryArchive


DAY = "2026-08-11"


def item(image_id: int, username: str, level: int) -> dict:
    return {
        "id": image_id,
        "postId": image_id,
        "username": username,
        "createdAt": datetime(2026, 8, 11, 18, image_id, tzinfo=timezone.utc).isoformat(),
        "url": f"https://example.invalid/{image_id}.jpg",
        "type": "image",
        "nsfwLevel": {1: "None", 2: "Soft", 4: "Mature"}[level],
        "browsingLevel": level,
        "stats": {"reactionCount": image_id},
    }


with tempfile.TemporaryDirectory(prefix="civitai-content-cache-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history", selected_browsing_levels=[1, 2])
    archive._upsert_normalized([item(1, "PGArtist", 1), item(2, "RArtist", 4)], DAY)
    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete,content_rating) VALUES(?,1,'Mature')", (DAY,))

    archive.build_artist_index(DAY)
    assert archive.day_summary(DAY)["artistCount"] == 1

    # Changing the preference itself must not eagerly rebuild every completed block.
    original_build = archive.build_artist_index
    archive.build_artist_index = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("content preference change rebuilt an index eagerly"))
    archive.set_content_filter([4])
    archive.build_artist_index = original_build

    # The first use of a combination builds it once.
    assert archive.day_summary(DAY)["artistCount"] == 1
    with archive.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM day_artist_cache_state WHERE day=?", (DAY,)).fetchone()[0] == 2

    # Returning to the first combination restores its saved index instead of scanning.
    archive.set_content_filter([1, 2])
    archive.build_artist_index = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("cached content index was rebuilt"))
    assert archive.day_summary(DAY)["artistCount"] == 1

print({"switchIsLazy": True, "firstUseBuildsOnce": True, "repeatUsesCache": True})

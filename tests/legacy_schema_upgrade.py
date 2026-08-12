"""An archive written by an older version must keep working after an upgrade.

Older builds carried columns the current schema no longer declares. CREATE TABLE
IF NOT EXISTS leaves those columns in place, so any positional INSERT silently
works on a fresh database and fails on every real user's archive.
"""

from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="civitai-legacy-schema-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive

    root = Path(temporary) / "history"
    archive = HistoryArchive(root)

    # Reproduce the drift: a column this schema dropped, still present on disk.
    database = root / "history.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE images ADD COLUMN stats_observed_at TEXT")
    with sqlite3.connect(database) as db:
        legacy = [row[1] for row in db.execute("PRAGMA table_info(images)")]
    assert "stats_observed_at" in legacy and len(legacy) == 20, legacy

    # Re-opening must not repair or drop it, and collection must still write.
    archive = HistoryArchive(root)
    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='8'%3E%3C/svg%3E"
    written = archive._upsert_normalized([
        {"id": 4242, "postId": 4242, "username": "LegacyArtist", "createdAt": f"{day}T10:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "baseModel": "Illustrious", "stats": {"reactionCount": 3}},
    ], forced_date=day)
    assert written == [4242], written

    # Updating the same image again must also survive (the ON CONFLICT path).
    archive._upsert_normalized([
        {"id": 4242, "postId": 4242, "username": "LegacyArtist", "createdAt": f"{day}T10:00:00Z",
         "url": pixel, "width": 8, "height": 8, "type": "image", "nsfwLevel": "None",
         "baseModel": "Pony", "stats": {"reactionCount": 9}},
    ], forced_date=day)

    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    archive.build_artist_index(day)

    # The upgraded archive is fully usable: it reads back, ranks, and filters by model.
    summary = archive.day_summary(day)
    assert summary["artistCount"] == 1 and summary["imageCount"] == 1, summary
    models = archive.day_models(day)
    assert models and models[0]["model"] == "Pony", models
    matching = archive.creators_using_models(day, ["Pony"])
    assert list(matching) == ["legacyartist"], matching
    assert archive.creators_using_models(day, ["Illustrious"]) == {}
    page = archive.artists_page(day, 0, 10)
    assert [row["username"] for row in page] == ["LegacyArtist"], page

print({"legacyColumnPreserved": True, "insertSurvivesDrift": True,
       "conflictUpdateSurvivesDrift": True, "modelFilterWorksAfterUpgrade": True})

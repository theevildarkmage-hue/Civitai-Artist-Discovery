"""Exercise the archive with concurrent request-style reads and writes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive


with tempfile.TemporaryDirectory(prefix="civitai-sqlite-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    day = "2026-07-31"
    archive._upsert_normalized([{  # Test fixture for the same normalized shape used by collection.
        "id": 1, "createdAt": "2026-07-31T12:00:00Z", "url": "https://image.civitai.com/test.jpeg",
        "username": "concurrency-test", "stats": {"reactionCount": 0}, "type": "image",
    }], forced_date=day)
    archive.build_artist_index(day)

    def exercise(worker: int) -> None:
        for iteration in range(30):
            archive.status(day)
            archive.day_summary(day)
            archive.artists_page(day, 0, 10)
            archive.artist_images(day, "concurrency-test")
            archive.update_stats(1, {"reactionCount": worker * 30 + iteration})

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(exercise, range(12)))

    with archive.connect() as database:
        journal = database.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = database.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal.lower() == "wal" and busy_timeout == 30000

print({"connections": "per-operation", "journalMode": "wal", "busyTimeoutMs": 30000, "concurrentWorkers": 12})

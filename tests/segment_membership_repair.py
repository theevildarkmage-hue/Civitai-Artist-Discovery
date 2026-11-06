"""Repair mislabeled full-day halves without deleting artwork or all-day galleries."""
import test_safety  # Refuse live application endpoints and isolate all test data.
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="cad3-discovery-segment-repair-") as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.history import HistoryArchive, bounds, parse_day
    from discovery.capture import segment_bounds

    history = HistoryArchive(Path(temporary) / "history", "Soft")
    value = "2026-10-13"
    start, end = bounds(parse_day(value))
    morning, midday = segment_bounds(parse_day(value), "morning")
    created = [start.isoformat(), (midday.replace(minute=0)).isoformat()]
    with history.connect() as db:
        for index, timestamp in enumerate(created, 1):
            db.execute("""INSERT INTO images(id,local_date,username,username_key,created_at,url,
                browsing_level,nsfw_level,model_version_ids,stats,resources)
                VALUES(?,?,?,?,?,?,1,1,'[]','{}','[]')""",
                (index, value, "synthetic", "synthetic", timestamp, "https://example.invalid/art"))
        for key in [value, value+"#morning", value+"#evening"]:
            db.execute("INSERT INTO days(day,complete,start_utc,end_utc) VALUES(?,1,?,?)",
                       (key, start.isoformat(), end.isoformat()))
            db.executemany("INSERT INTO block_images(block_key,image_id) VALUES(?,?)", [(key,1),(key,2)])
    repaired = history.repair_full_day_segment_memberships()
    assert len(repaired) == 2
    with history.connect() as db:
        assert db.execute("SELECT count(*) FROM images").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM block_images WHERE block_key=?", (value,)).fetchone()[0] == 2
        assert [x[0] for x in db.execute("SELECT image_id FROM block_images WHERE block_key=?", (value+"#morning",))] == [1]
        assert [x[0] for x in db.execute("SELECT image_id FROM block_images WHERE block_key=?", (value+"#evening",))] == [2]
    assert history.repair_full_day_segment_memberships() == []
    assert len(history.artist_images(value+"#morning", "synthetic")) == 1
    assert len(history.artist_images(value+"#evening", "synthetic")) == 1
    # Fail closed without modifying the earlier valid candidate in the same transaction.
    with history.connect() as db:
        db.execute("UPDATE days SET start_utc=?,end_utc=? WHERE day LIKE '%#%'", (start.isoformat(),end.isoformat()))
        db.execute("DELETE FROM block_images WHERE block_key=? AND image_id=2", (value,))
    try:
        history.repair_full_day_segment_memberships()
        raise AssertionError("Missing preserved artwork must refuse repair")
    except ValueError:
        pass
    with history.connect() as db:
        assert db.execute("SELECT end_utc FROM days WHERE day=?", (value+"#morning",)).fetchone()[0] == end.isoformat()
    print({"separateHalves": True, "noArtworkDeletion": True, "fullDayPreserved": True,
           "idempotent": True, "unsafeRepairRollsBack": True})

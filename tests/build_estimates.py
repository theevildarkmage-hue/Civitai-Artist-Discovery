"""Build estimates scale with coverage and omit halves already ready at that coverage."""

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import HistoryArchive


SOURCE = "2026-08-11"
TARGET = "2026-08-12"


def item(image_id: int, username: str, level: int, hour: int) -> dict:
    return {"id": image_id, "postId": image_id, "username": username,
        "createdAt": datetime(2026, 8, 11, hour, image_id % 60, tzinfo=timezone.utc).isoformat(),
        "url": f"https://example.invalid/{image_id}.jpg", "type": "image",
        "nsfwLevel": {1: "None", 2: "Soft", 4: "Mature", 8: "X", 16: "X"}[level],
        "browsingLevel": level, "stats": {}}


with tempfile.TemporaryDirectory(prefix="civitai-build-estimates-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    rows = []
    image_id = 1
    for hour, segment in ((6, "morning"), (18, "evening")):
        for level in (1, 2, 4, 8, 16):
            for _ in range(level):
                rows.append(item(image_id, f"Artist{image_id}", level, hour))
                image_id += 1
        archive._upsert_normalized(rows[-31:], SOURCE)
        key = f"{SOURCE}#{segment}"
        with archive.connect() as db:
            db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                           [(key, row["id"]) for row in rows[-31:]])
            db.execute("""INSERT INTO days(day,complete,content_rating,elapsed_seconds,
                          seek_seconds,organize_seconds,updated_at) VALUES(?,1,'X',600,60,0,?)""",
                       (key, datetime.now(timezone.utc).isoformat()))

    safe = archive.build_estimate("all", "Soft", TARGET)["seconds"]
    mature = archive.build_estimate("all", "Mature", TARGET)["seconds"]
    explicit = archive.build_estimate("all", "X", TARGET)["seconds"]
    assert safe < mature < explicit, (safe, mature, explicit)
    evidence = archive.build_estimate("all", "X", TARGET)
    assert evidence["fixedBenchmark"] and evidence["benchmarkImages"] == 82050, evidence
    assert evidence["listingRequests"] == 458 and evidence["seekRequests"] == 170, evidence
    assert evidence["lowSeconds"] == 3140 and evidence["highSeconds"] == 4595, evidence
    half_safe = archive.build_estimate("morning", "Soft", TARGET)
    assert half_safe["listingRequests"] == 70 and half_safe["seekRequests"] == 34, half_safe
    assert half_safe["lowSeconds"] >= 6 * 60, half_safe

    # A Soft morning is complete. A Soft full-day estimate now includes only evening,
    # while an X estimate still includes both because Morning needs a coverage upgrade.
    with archive.connect() as db:
        db.execute("INSERT INTO days(day,complete,content_rating) VALUES(?,1,'Soft')",
                   (f"{TARGET}#morning",))
    assert archive.build_estimate("all", "Soft", TARGET)["seconds"] == \
        archive.build_estimate("evening", "Soft", TARGET)["seconds"]
    assert archive.build_estimate("all", "X", TARGET)["seconds"] == explicit

print({"coverageChangesEstimate": True, "readyHalfExcluded": True,
       "coverageUpgradeIncluded": True})

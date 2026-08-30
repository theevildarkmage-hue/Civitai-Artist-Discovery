"""Reactions refresh in 100-image batches, and a detail lookup names its browsing level."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.history import (ALL_BROWSING_LEVELS, FEED_FLOOR_PROBE_OFFSET,
                               REACTION_SWEEP_BATCH, HistoryArchive)

DAY = "2026-07-31"


def image(image_id: int, level: int = 4) -> dict:
    return {"id": image_id, "postId": image_id, "username": "Artist",
            "createdAt": f"{DAY}T06:00:00.000Z", "url": f"http://x/{image_id}.jpg",
            "nsfwLevel": "Mature", "browsingLevel": level, "width": 8, "height": 8,
            "type": "image", "baseModel": "Test", "modelVersionIds": [],
            "prompt": "", "negativePrompt": "", "resources": [],
            "stats": {"reactionCount": 1, "likeCount": 1}}


class FakeClient:
    """Batches like SocialClient, and records how many round trips it took."""

    def __init__(self, known):
        self.known, self.calls = known, 0

    def batch_query_optional(self, procedure, payloads):
        assert procedure == "image.get", procedure
        assert len(payloads) <= REACTION_SWEEP_BATCH, len(payloads)
        self.calls += 1
        return [self.known.get(int(p["id"])) for p in payloads]


with tempfile.TemporaryDirectory(prefix="civitai-reactions-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history", "X")
    ids = list(range(1, 251))
    archive._upsert_normalized([image(i) for i in ids], forced_date=DAY)
    with archive.connect() as db:
        db.executemany("INSERT OR IGNORE INTO block_images(block_key,image_id) VALUES(?,?)",
                       [(DAY, i) for i in ids])

    # Everything is unfetched, so everything is stale.
    stale = archive.stale_reaction_ids(DAY)
    assert stale == ids, (len(stale), len(ids))

    # One image is missing upstream; the rest report fresh counts.
    known = {i: {"stats": {"likeCountAllTime": 5, "heartCountAllTime": 2,
                           "laughCountAllTime": 0, "cryCountAllTime": 0,
                           "commentCountAllTime": 3, "collectedCountAllTime": 1,
                           "dislikeCountAllTime": 0}} for i in ids if i != 7}
    client = FakeClient(known)
    done = archive.sweep_image_reactions(client, stale)
    assert done == len(ids) - 1, done
    # 250 images must cost 3 requests, not 250.
    assert client.calls == 3, client.calls

    saved = json.loads(archive.stats(1))if isinstance(archive.stats(1), str) else archive.stats(1)
    assert saved["reactionCount"] == 7 and saved["commentCount"] == 3, saved

    # A refreshed image is no longer stale; the one that did not answer still is, so the
    # next sweep retries it rather than skipping it forever.
    still = archive.stale_reaction_ids(DAY)
    assert still == [7], still

    # Ageing out brings them all back for a refresh, because reactions keep moving.
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    with archive.connect() as db:
        db.execute("UPDATE images SET stats_fetched_at=? WHERE id<>7", (old,))
    assert len(archive.stale_reaction_ids(DAY, max_age_hours=12)) == len(ids), "stale window"
    assert archive.stale_reaction_ids(DAY, max_age_hours=72) == [7], "fresh window"

    # The detail lookup must name a browsing level, or Civitai answers at public only.
    seen = {}

    def request(params, **_):
        seen.update(params)
        return {"items": []}, 0

    archive._request = request
    archive.detail(1)
    assert int(seen["browsingLevel"]) == 4, seen
    assert seen["imageId"] == 1 and seen["withMeta"] == "true", seen
    # Nothing came back, so it is recorded as attempted instead of retried forever.
    with archive.connect() as db:
        assert db.execute("SELECT details_loaded FROM images WHERE id=1").fetchone()[0] == 1
    calls_before = dict(seen)
    seen.clear()
    archive.detail(1)
    assert not seen, "a resolved detail must not re-request"

print({"batchSize": REACTION_SWEEP_BATCH, "requestsFor250": 3, "allLevels": ALL_BROWSING_LEVELS,
       "missingImageRetried": True, "staleWindowHonoured": True, "detailNamesLevel": True})

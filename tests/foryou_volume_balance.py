"""For You rewards representative quality without letting upload volume buy rank."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="foryou-volume-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server
    from discovery.history import HistoryArchive

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    history = HistoryArchive(Path(temporary) / "history")
    server.HISTORY = history

    def artwork(image_id, username, reactions, minute):
        return {"id": image_id, "postId": image_id, "username": username,
                "createdAt": f"{day}T12:{minute % 60:02d}:00Z",
                "url": "https://image.civitai.com/test.jpeg", "width": 8, "height": 8,
                "type": "image", "nsfwLevel": "None",
                "stats": {"reactionCount": reactions}}

    # One genuinely strong small set versus one hundred average images. Raw totals put
    # Volume first (300 versus 90), while the personalised quality measure must not.
    items = [artwork(1 + index, "StrongSet", value, index)
             for index, value in enumerate((24, 20, 18, 16, 12))]
    items += [artwork(1000 + index, "Volume", 3, index) for index in range(100)]
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    assert history.day_artist_keys(day)[0]["key"] == "volume"
    quality = history.creator_quality_scores(day)
    assert quality["strongset"] > quality["volume"], quality

    representatives = {row["key"]: row["representativeId"]
                       for row in history.day_artist_keys(day)}

    class EqualTaste:
        def score_images(self, image_ids):
            return {image_id: 1.0 for image_id in image_ids}

        def follower_counts(self, usernames):
            return {username.casefold(): 5000 for username in usernames}

    original = server.TASTE
    server.TASTE = EqualTaste()
    try:
        order, total = server.day_view_order(day, "foryou", None,
                                             {"followed": set(), "reacted": {}})
    finally:
        server.TASTE = original

    assert total == 2, (order, total)
    assert order == ["strongset", "volume"], (order, quality, representatives)

    # The real gallery contains several recommendation lanes. Preserve the scored order
    # inside those lanes, but never allow a row to become majority prolific while a
    # normal-volume alternative is waiting. No creator is discarded.
    raw = [{"username": f"heavy-{index}", "imageCount": 100} for index in range(6)] + \
          [{"username": f"regular-{index}", "imageCount": 5} for index in range(8)]
    balanced = server.balance_posting_volume(raw)
    assert {row["username"] for row in balanced} == {row["username"] for row in raw}
    assert len(balanced) == len(raw)
    for start in range(len(balanced) - 4):
        window = balanced[start:start + 5]
        assert sum(row["imageCount"] > 20 for row in window) <= 2, window

print({"rawTotalLeader": "volume", "forYouLeader": "strongset",
       "volumePosts": 100, "qualityPosts": 5, "maxProlificPerFive": 2})

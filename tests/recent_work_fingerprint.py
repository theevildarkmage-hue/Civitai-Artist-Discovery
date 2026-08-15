"""Recent posts distinguish strong creative signals from generic tags."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.taste import TasteStore


class FakeClient:
    def __init__(self):
        self.calls = []
        self.model_calls = []
        self.new_ids = []

    def creator_images_page(self, username, cursor=None, limit=200):
        self.calls.append(cursor)
        ids = ([*self.new_ids, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
               if cursor is None else [11, 12, 13, 14, 15])
        items = []
        for image_id in ids:
            tags = [{"name": "generic"}]
            if image_id <= 5:
                tags.append({"name": "distinctive"})
            items.append({"id": image_id, "createdAt": f"2026-08-{image_id:02d}T00:00:00Z",
                          "tags": tags, "modelVersionIds": [99] if image_id <= 8 else [100]})
        return {"items": items, "nextCursor": "older" if cursor is None else None}

    def public_model_version(self, model_version_id):
        self.model_calls.append(model_version_id)
        return {"id": model_version_id, "name": f"Version {model_version_id}",
                "modelId": model_version_id + 1000,
                "model": {"name": f"Model {model_version_id}"}}


with tempfile.TemporaryDirectory(prefix="recent-work-") as temporary:
    store = TasteStore(Path(temporary) / "taste")
    with store.connect() as db:
        # Generic occurs in 90% of the comparison sample, so 100% here is not a strong
        # fingerprint. Distinctive occurs in only 5%, making its 50% share meaningful.
        db.executemany("INSERT INTO tag_baseline(tag_id,tag_name,image_count) VALUES(?,?,?)",
                       [(1, "generic", 90), (2, "distinctive", 5)])
        store._set_state(db, "baseline_images", 100)
        db.executemany("INSERT INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                       [(501, "generic"), (501, "distinctive"), (502, "generic")])

    client = FakeClient()
    assert store.refresh_recent_work(client, "TestUser") == 15
    assert client.calls == [None, "older"], client.calls
    weights, evidence = store._recent_tag_stats()
    assert "generic" not in weights, weights
    assert "distinctive" in weights and evidence["distinctive"]["lift"] == 6.7, evidence
    scores = store.score_image_components([501, 502])
    assert scores[501]["recent"] > 0, scores
    assert 502 not in scores or scores[502]["recent"] == 0, scores
    assert store.recent_model_weights() == {99: 8 / 15, 100: 7 / 15}
    summary = store.recent_work_summary()
    assert summary["images"] == 15 and summary["complete"] and summary["strongTagCount"] == 1, summary
    assert summary["models"][0]["modelName"] == "Model 99", summary["models"]
    assert summary["models"][0]["versionName"] == "Version 99", summary["models"]
    assert client.model_calls == [99, 100], client.model_calls

    # Once complete, the newest page is enough: store new uploads and stop as soon as
    # that page reaches ids already in the archive. The historical page is not re-read.
    client.calls.clear()
    client.new_ids = [101, 102]
    assert store.refresh_recent_work(client, "TestUser") == 2
    assert client.calls == [None], client.calls
    assert client.model_calls == [99, 100], client.model_calls
    assert store.recent_work_summary()["images"] == 17

print({"historicalFingerprint": 15, "incrementalAdds": 2, "secondRunPages": 1,
       "genericTagRejected": True, "distinctiveTagLift": 6.7,
       "modelFrequencyCached": True})

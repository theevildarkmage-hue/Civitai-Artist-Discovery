"""Opening an unswept image fetches and permanently caches only that image's tags."""

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


with tempfile.TemporaryDirectory(prefix="detail-tags-demand-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    from discovery.taste import TasteStore

    store = TasteStore(Path(temporary) / "discovery")

    class Client:
        calls = 0

        def batch_query_optional(self, procedure, payloads):
            self.calls += 1
            assert procedure == "tag.getVotableTags"
            assert payloads == [{"id": 4242, "type": "image"}]
            return [[{"name": "Portrait"}, {"name": "Blue Hair"},
                     {"name": "portrait"}, {"name": ""}]]

    client = Client()
    assert store.image_tags(4242) == {"known": False, "tags": []}
    first = store.ensure_image_tags(client, 4242)
    assert first == {"known": True, "tags": [
        {"name": "blue hair", "hidden": False},
        {"name": "portrait", "hidden": False}]}, first
    # The second details click is completely local.
    assert store.ensure_image_tags(client, 4242) == first
    assert client.calls == 1, client.calls

    # A valid empty Civitai response is remembered as genuinely untagged rather than
    # being fetched again on every click.
    class EmptyClient:
        calls = 0

        def batch_query_optional(self, procedure, payloads):
            self.calls += 1
            return [[]]

    empty = EmptyClient()
    assert store.ensure_image_tags(empty, 4343) == {"known": True, "tags": []}
    assert store.ensure_image_tags(empty, 4343) == {"known": True, "tags": []}
    assert empty.calls == 1

    class PreviewClient:
        calls = 0

        def batch_query_optional(self, procedure, payloads):
            self.calls += 1
            assert payloads == [{"id": 5001, "type": "image"},
                                {"id": 5002, "type": "image"}]
            return [[{"name": "safe"}], [{"name": "blocked-topic"}]]

    with store.connect() as db:
        db.execute("INSERT INTO hidden_tags(tag_id,tag_name) VALUES(1,'blocked-topic')")
    previews = PreviewClient()
    checked = store.ensure_image_tags_many(previews, [5001, 5002, 5001])
    assert checked[5001]["tags"] == [{"name": "safe", "hidden": False}], checked
    assert checked[5002]["tags"] == [{"name": "blocked-topic", "hidden": True}], checked
    assert previews.calls == 1
    # Both cards are now local; a repeated viewport pass cannot make another API call.
    store.ensure_image_tags_many(previews, [5001, 5002])
    assert previews.calls == 1

print({"singleImageFetched": True, "cachedAfterClick": True,
       "emptyResponseRemembered": True, "previewBatchFetchedOnce": True,
       "hiddenTagReturnedBeforePreview": True})

"""Card dates and action-menu persistence stay truthful and account-scoped."""

from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.social import SocialClient
from discovery.taste import TasteStore


with tempfile.TemporaryDirectory(prefix="civitai-card-actions-") as temporary:
    store = TasteStore(Path(temporary))

    class CivitaiPreferences:
        def query(self, procedure, payload):
            assert procedure == "hiddenPreferences.getHidden"
            return {"hiddenUsers": [{"id": 42, "username": "Silly_Goose_92", "hidden": True}],
                    "blockedUsers": [], "blockedByUsers": [], "hiddenTags": [],
                    "hiddenImages": []}

    store.import_hidden_preferences(CivitaiPreferences())
    assert store.hidden_creator_keys() == {"silly_goose_92"}
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='app_hidden_creators'").fetchone()


class FakeSocial(SocialClient):
    def __init__(self):
        super().__init__()
        self.mutation = None

    def query(self, procedure, payload):
        assert procedure == "collection.getAllUser"
        assert payload["type"] == "Image"
        return [{"id": 12, "name": "Favorites", "userId": 7, "read": "Private"}]

    def mutate(self, procedure, payload):
        self.mutation = (procedure, payload)
        return {"status": "added"}


client = FakeSocial()
collections = client.writable_image_collections(7)
client.add_image_to_collection(99, collections[0])
assert client.mutation == ("collection.saveItem", {
    "type": "Image", "imageId": 99,
    "collections": [{"collectionId": 12, "userId": 7, "read": "Private"}],
    "removeFromCollectionIds": [],
})
client.hide_user(42, "Silly_Goose_92")
assert client.mutation == ("hiddenPreferences.toggleHidden", {
    "kind": "user", "data": [{"id": 42, "username": "Silly_Goose_92"}],
    "hidden": True,
})

cards = Path("static/ui/cards.js").read_text(encoding="utf-8")
oauth = Path("discovery/oauth.py").read_text(encoding="utf-8")
server = Path("server.py").read_text(encoding="utf-8")
assert "ago(current.createdAt)" not in cards
assert "toLocaleDateString" in cards
assert "data-action=\"collections\"" in cards and "data-action=\"hide\"" in cards
assert "/api/content-controls/hide-artist" in cards
assert "/api/hidden-creators" not in cards
assert "READ_SCOPE | USER_WRITE | COLLECTIONS_READ" in oauth
assert 'client.hide_user(user_id' in server and "TASTE.import_hidden_preferences(client)" in server
assert 'class="card-nav-status"' in cards, "fresh cards would render as an unexplained black box"
assert "await waitForArtwork()" in cards, "loading feedback ends before artwork renders"

print({"absoluteCardDates": True, "civitaiHiddenUsersAreMirrored": True,
       "civitaiHidePayload": True, "collectionPayload": True,
       "initialArtworkFeedback": True})

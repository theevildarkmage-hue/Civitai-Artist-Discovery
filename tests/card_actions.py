"""Card dates and action-menu persistence stay truthful and account-scoped."""

from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.social import SocialClient
from discovery.taste import TasteStore


with tempfile.TemporaryDirectory(prefix="civitai-card-actions-") as temporary:
    store = TasteStore(Path(temporary))
    store.hide_creator("Silly_Goose_92")
    assert "silly_goose_92" in store.hidden_creator_keys()
    assert store.app_hidden_creators()[0]["username"] == "Silly_Goose_92"
    class EmptyPreferences:
        def query(self, procedure, payload):
            return {"hiddenUsers": [], "blockedUsers": [], "blockedByUsers": [],
                    "hiddenTags": [], "hiddenImages": []}

    store.import_hidden_preferences(EmptyPreferences())
    assert "silly_goose_92" in store.hidden_creator_keys(), "Civitai refresh erased a local hide"
    store.unhide_creator("SILLY_GOOSE_92")
    assert not store.app_hidden_creators()


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

cards = Path("static/ui/cards.js").read_text(encoding="utf-8")
assert "ago(current.createdAt)" not in cards
assert "toLocaleDateString" in cards
assert "data-action=\"collections\"" in cards and "data-action=\"hide\"" in cards

print({"absoluteCardDates": True, "localHideIsReversible": True,
       "localHideSurvivesCivitaiRefresh": True, "collectionPayload": True})

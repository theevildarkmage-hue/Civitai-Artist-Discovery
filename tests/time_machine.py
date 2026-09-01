"""Each followed creator is walked from their oldest image, one card at a time."""

from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.timemachine import PRIME_PAGE_SIZE, TimeMachine


def image(image_id: int, name: str, day: int, level: int = 1) -> dict:
    return {"id": image_id, "postId": image_id, "username": name,
            "createdAt": f"2025-01-{day:02d}T00:00:00.000Z",
            "url": f"http://example/{image_id}.jpg", "nsfwLevel": "None",
            "browsingLevel": level, "width": 8, "height": 8, "type": "image",
            "stats": {"likeCount": 1}, "meta": None}


class FakeArchive:
    """Supplies the paced request lane the real archive owns."""

    def __init__(self, pages):
        self.pages = pages          # {(username, cursor): (items, nextCursor)}
        self.calls = []
        self.content_rating = "Soft"
        self.visible_levels = (1, 2)

    def _request(self, params, **_):
        assert params["sort"] == "Oldest", params
        assert params["limit"] == PRIME_PAGE_SIZE, params
        name, cursor = params["username"], params.get("cursor")
        self.calls.append((name, cursor))
        items, nxt = self.pages.get((name, cursor), ([], None))
        return {"items": items, "metadata": {"nextCursor": nxt}}, 100


class FakeTaste:
    def __init__(self, path, ids):
        self.path = path
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE followed_creators(creator_id INTEGER PRIMARY KEY)")
            db.executemany("INSERT INTO followed_creators VALUES(?)", [(i,) for i in ids])
            db.commit()
        finally:
            db.close()

    def connect(self):
        from contextlib import contextmanager

        @contextmanager
        def _open():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()
        return _open()


class FakeClient:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, 0

    def batch_query_optional(self, procedure, payloads):
        assert procedure == "user.getCreator", procedure
        self.calls += 1
        return [{"username": self.mapping.get(int(p["id"]))} for p in payloads]


with tempfile.TemporaryDirectory(prefix="civitai-timemachine-",
                                 ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    taste = FakeTaste(root / "taste.sqlite3", [11, 22])
    # Ana has two pages; Bo's history ends inside the first, as most creators' do.
    pages = {
        ("Ana", None): ([image(1, "Ana", 1), image(2, "Ana", 2), image(3, "Ana", 3, level=4)], "c1"),
        ("Ana", "c1"): ([image(4, "Ana", 4)], None),
        ("Bo", None): ([image(9, "Bo", 5)], None),
    }
    archive = FakeArchive(pages)
    machine = TimeMachine(root, archive, taste)
    client = FakeClient({11: "Ana", 22: "Bo"})

    names = machine.followed_usernames(client)
    assert names == ["Ana", "Bo"], names
    assert client.calls == 1, "483 follows should resolve in a handful of batched calls"

    result = machine.prime(client)
    assert result["primed"] == 2, result
    # One request per creator, and none of them a second page.
    assert archive.calls == [("Ana", None), ("Bo", None)], archive.calls

    state = machine.status()
    assert state["creators"] == 2 and state["primed"] == 2, state
    assert state["progress"] == 100.0, state

    # Bo's history ended inside the first page, so Bo is complete; Ana is not.
    cards = {c["username"]: c for c in machine.cards()}
    assert set(cards) == {"Ana", "Bo"}, cards
    # Shaped like a gallery artist, so the browser builds these with the same card
    # factory: one image, which is what hides the carousel.
    assert cards["Ana"]["imageCount"] == 1 and cards["Ana"]["representativeIndex"] == 0
    assert cards["Ana"]["representative"]["id"] == 1, cards["Ana"]
    assert cards["Ana"]["complete"] is False, cards["Ana"]
    assert cards["Bo"]["representative"]["id"] == 9 and cards["Bo"]["complete"] is True
    # The level-4 image is not visible at Soft, so it is not counted or shown.
    assert cards["Ana"]["knownCount"] == 2, cards["Ana"]
    assert cards["Ana"]["seenCount"] == 0, cards["Ana"]

    # Cards come back least-recently-advanced first. Plain alphabetical put the same
    # creators at the top of every visit, and it sorted case-sensitively while the browser
    # inserted by a lowercased key, so the grid reshuffled on every refresh.
    import time as _time
    assert [c["username"] for c in machine.cards()] == ["Ana", "Bo"]

    # Scrolling past Ana advances only Ana.
    _time.sleep(0.01)
    assert machine.advance(["Ana"]) == 1
    assert [c["username"] for c in machine.cards()] == ["Bo", "Ana"], "a creator just read must sink"
    cards = {c["username"]: c for c in machine.cards()}
    assert cards["Ana"]["representative"]["id"] == 2, cards["Ana"]
    assert cards["Ana"]["seenCount"] == 1, cards["Ana"]
    assert cards["Bo"]["representative"]["id"] == 9, "advancing one must not move another"

    # Advancing past the hidden level-4 image lands on nothing further in this page,
    # so Ana drops out until refilled.
    machine.advance(["Ana"]); machine.advance(["Ana"])
    assert "Ana" not in {c["username"] for c in machine.cards()}, "exhausted page should hide"

    added = machine.refill("Ana")
    assert added == 1, added
    assert archive.calls[-1] == ("Ana", "c1"), archive.calls
    cards = {c["username"]: c for c in machine.cards()}
    assert cards["Ana"]["representative"]["id"] == 4, cards["Ana"]
    assert cards["Ana"]["complete"] is True, cards["Ana"]

    # A creator read to the end stays gone rather than looping.
    machine.advance(["Ana"])
    assert "Ana" not in {c["username"] for c in machine.cards()}
    assert machine.refill("Ana") == 0, "a finished creator must not be refetched"

    # Bo was complete from the first page and never needs a request.
    before = len(archive.calls)
    assert machine.refill("Bo") == 0
    assert len(archive.calls) == before, "no request for an already-complete creator"

    # The content-control check refuses images the app has not collected. These are
    # collected, just not into the daily archive, so they must be recognised -- otherwise
    # every card fails its check and the tab shows no artwork at all.
    assert machine.has_image(1) and machine.has_image(9), "collected images must be known"
    assert not machine.has_image(123456), "unknown images must stay refused"

    # Reacting reads and writes stats through the store that holds the row. Reading them
    # from the daily archive returned {}, so a like wrote 1 instead of the count plus one.
    assert machine.stats(1) == {"likeCount": 1, "reactionCount": 1}, machine.stats(1)
    bumped = {**machine.stats(1), "likeCount": 2, "reactionCount": 2}
    machine.update_stats(1, bumped)
    assert machine.stats(1) == bumped, machine.stats(1)
    assert machine.stats(123456) == {}, "an unknown image has no stats to report"

    # The detail dialog needs a payload for these images too, or its controls do nothing.
    detail = machine.detail(1)
    assert detail["id"] == 1 and detail["username"] == "Ana", detail
    assert detail["thumbnailUrl"] and detail["civitaiUrl"], detail
    assert "prompt" in detail and "resources" in detail, detail
    assert detail["stats"]["likeCount"] == 2, detail
    try:
        machine.detail(123456)
        raise AssertionError("an unknown image must not produce a detail payload")
    except ValueError:
        pass

print({"resolvedInBatches": True, "onePagePerCreator": True, "oldestFirst": True,
       "levelFiltered": True, "pointerPerCreator": True, "refillsOnDemand": True,
       "exhaustedStaysDone": True, "galleryCardShape": True, "contentCheckAccepts": True})

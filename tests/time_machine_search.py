"""Finding one followed creator and reading only their history, from the saved spot."""

from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.timemachine import PRIME_PAGE_SIZE, TimeMachine


def image(image_id: int, name: str, level: int = 1) -> dict:
    return {"id": image_id, "postId": image_id, "username": name,
            "createdAt": f"2025-01-01T00:00:{image_id % 60:02d}.000Z",
            "url": f"http://example/{image_id}.jpg", "nsfwLevel": "None",
            "browsingLevel": level, "width": 8, "height": 8, "type": "image",
            "stats": {}, "meta": None}


class FakeArchive:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
        self.content_rating, self.visible_levels = "Soft", (1, 2)

    def _request(self, params, **_):
        assert params["sort"] == "Oldest" and params["limit"] == PRIME_PAGE_SIZE, params
        key = (params["username"], params.get("cursor"))
        self.calls.append(key)
        items, nxt = self.pages.get(key, ([], None))
        return {"items": items, "metadata": {"nextCursor": nxt}}, 100


with tempfile.TemporaryDirectory(prefix="civitai-tm-search-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    archive = FakeArchive({
        ("Ana_Art", None): ([image(i, "Ana_Art") for i in range(1, 6)] + [image(6, "Ana_Art", level=4)], "c1"),
        ("Ana_Art", "c1"): ([image(i, "Ana_Art") for i in range(7, 10)], None),
        ("banana", None): ([image(20, "banana")], None),
        ("AnaXart", None): ([image(30, "AnaXart")], None),
    })
    machine = TimeMachine(root, archive, taste=None)
    for name in ("Ana_Art", "banana", "AnaXart"):
        machine.fetch_page(name)

    # Contains-match, name-prefix first; "_" is literal, not a LIKE wildcard.
    names = [c["username"] for c in machine.search_creators("ana")]
    assert names[:2] == ["Ana_Art", "AnaXart"] and names[2] == "banana", names
    assert [c["username"] for c in machine.search_creators("ana_")] == ["Ana_Art"]
    assert machine.search_creators("   ") == []
    # Suggestions never touch Civitai.
    calls = len(archive.calls)
    machine.search_creators("a")
    assert len(archive.calls) == calls

    # First page starts at the saved spot and skips levels the reader cannot see.
    machine.advance_to("Ana_Art", 1)          # passed positions 0 and 1
    page = machine.creator_history("ana_art", limit=3)
    assert [i["id"] for i in page["images"]] == [3, 4, 5], page["images"]
    assert page["seenCount"] == 2 and page["hasMore"], page

    # Paging continues from the list, reaching past the cache fetches the next page.
    page = machine.creator_history("Ana_Art", after=page["images"][-1]["position"], limit=10)
    assert [i["id"] for i in page["images"]] == [7, 8, 9], page["images"]
    assert archive.calls[-1] == ("Ana_Art", "c1"), archive.calls
    assert page["complete"] and not page["hasMore"], page

    # The spot only moves forward, so a late or duplicate flush cannot rewind it.
    assert machine.advance_to("Ana_Art", 4)
    assert not machine.advance_to("Ana_Art", 2)
    walk = {c["username"]: c for c in machine.cards()}
    assert walk["Ana_Art"]["representative"]["id"] == 7, "the walk must share the saved spot"

    # Only creators the Time Machine tracks.
    try:
        machine.creator_history("stranger")
        raise AssertionError("an unfollowed creator must be refused")
    except KeyError:
        pass

print({"searchPrefixFirst": True, "underscoreLiteral": True, "noRemoteSuggest": True,
       "startsAtSavedSpot": True, "refillsWhileScrolling": True, "spotMonotonic": True,
       "sharedWithWalk": True, "followedOnly": True})

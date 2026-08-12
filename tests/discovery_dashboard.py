"""Discovery dashboard analysis: reconciliation, rounding, isolation, and reset."""

from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discovery.taste as taste
from discovery.taste import TasteStore, _percentages

ACCOUNT = 4242
OTHER_ACCOUNT = 9999


def image(image_id, reactions, creator=("art_one", 11), tags=(("woman", 1), ("katana", 2)), **extra):
    username, creator_id = creator
    return {"id": image_id, "postId": image_id, "nsfwLevel": 1, "hasMeta": True,
            "createdAt": "2026-07-31T12:00:00Z", "stats": {"reactionCount": 3},
            "user": {"id": creator_id, "username": username},
            "reactions": [{"userId": ACCOUNT, "reaction": name} for name in reactions],
            "tags": [{"id": tag_id, "name": name, "source": "WD14"} for name, tag_id in tags],
            **extra}


def run_sync(store, pages, following, baseline=(), candidates=None, followers=None):
    """Drive one sync with canned responses instead of Civitai."""
    reacted = list(pages)
    sample = list(baseline)
    by_tag = dict(candidates or {})

    def images_page(self, *, cursor=None, limit=100, reactions=None, with_tags=True,
                    tags=None, period="AllTime"):
        if tags:  # candidate search for one taste signal
            return {"items": by_tag.get(tags[0], []), "nextCursor": None}
        queue = reacted if reactions else sample
        items = queue.pop(0) if queue else []
        return {"items": items, "nextCursor": "more" if queue else None}

    taste.SocialClient.images_page = images_page
    taste.SocialClient.query = lambda self, procedure, payload: list(following)
    taste.SocialClient.batch_query_optional = lambda self, procedure, payloads: [
        {"username": p["username"], "stats": {"followerCountAllTime": (followers or {}).get(p["username"])}}
        for p in payloads]
    taste.auth_status = lambda: {"id": store._account, "connected": True}
    store.start_sync()
    deadline = time.monotonic() + 20
    while store.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    state = store.status()
    assert not state["running"] and not state["error"], state
    return state


# Rounding holds for awkward distributions and for no reactions at all.
assert sum(entry["percent"] for entry in _percentages([("Like", 1), ("Heart", 1), ("Laugh", 1)])) == 100.0
assert sum(entry["percent"] for entry in _percentages([("Like", 2), ("Heart", 1)])) == 100.0
assert all(entry["percent"] == 0.0 for entry in _percentages([("Like", 0), ("Heart", 0)]))

with tempfile.TemporaryDirectory(prefix="civitai-discovery-", ignore_cleanup_errors=True) as temporary:
    store = TasteStore(Path(temporary) / "discovery")
    store._account = ACCOUNT
    taste.MIN_PAUSE = taste.MAX_PAUSE = 0.0

    assert store.summary() == {"hasData": False, "reactedImages": 0, "followedCreators": 0,
                               "lastSyncAt": None}

    # Two pages, a repeated tag id, a creator with no username, and a two-reaction image.
    first = [image(1, ["Like"]), image(2, ["Heart"], creator=("art_two", 12)),
             image(3, ["Like", "Heart"], tags=(("woman", 1), ("woman", 1), ("katana", 2)))]
    second = [image(4, ["Like"], creator=("art_three", 13)),
              image(5, ["Laugh"], creator=(None, None))]
    # "woman" saturates the sample; "katana" is rare in it, so only "katana" earns lift.
    baseline = [[image(90 + n, [], tags=(("woman", 1),)) for n in range(9)]
                + [image(99, [], tags=(("woman", 1), ("katana", 2)))]]
    # Candidates for the one signal tag that will survive lift: katana (id 2). The pool
    # deliberately contains a followed creator, an already-reacted creator, and the user.
    katana_pool = [
        image(500, [], creator=("art_one", 11)),          # already followed
        image(501, [], creator=("art_two", 12)),          # already reacted to
        image(502, [], creator=("me", ACCOUNT)),          # the user themselves
        image(503, [], creator=("art_new", 20)),
        image(504, [], creator=("art_new", 20)),
        image(505, [], creator=("art_small", 21)),
    ]
    run_sync(store, [first, second], following=[11, 77], baseline=baseline,
             candidates={2: katana_pool},
             followers={"art_new": 5000, "art_small": 120, "art_two": 640, "art_one": 12000})

    summary = store.summary()
    assert summary["reactedImages"] == 5, summary
    assert summary["reactionRecords"] == 6, summary
    assert summary["followedCreators"] == 2
    # The creator with no username is excluded from creator rankings but still counted above.
    assert summary["creatorsReactedTo"] == 3, summary
    # Below the 5-reaction "worth following" bar, a single reaction from each of
    # art_two and art_three does not count as not-followed here.
    assert summary["creatorsNotFollowed"] == 0, summary
    assert sum(entry["percent"] for entry in summary["reactionMix"]) == 100.0
    assert {entry["reaction"]: entry["count"] for entry in summary["reactionMix"]} == {
        "Like": 3, "Heart": 2, "Laugh": 1, "Cry": 0}
    # Tag rows deduplicate by id, so the repeat inside image 3 counts once.
    assert dict((tag["name"], tag["images"]) for tag in summary["topTags"])["woman"] == 5
    assert summary["reactedNotFollowed"] == [], summary["reactedNotFollowed"]
    # Follower counts reach every creator panel regardless of the "worth following" bar.
    by_name = {creator["username"]: creator for creator in summary["topCreators"]}
    assert by_name["art_two"]["followers"] == 640 and by_name["art_two"]["emerging"] is True
    # A creator whose count could not be read is reported as unknown, never as zero.
    assert by_name["art_three"]["followers"] is None and by_name["art_three"]["emerging"] is False
    top = {creator["username"]: creator for creator in summary["topCreators"]}
    assert top["art_one"]["followers"] == 12000 and top["art_one"]["emerging"] is False
    assert [creator["following"] for creator in summary["topCreators"] if creator["username"] == "art_one"] == [True]
    # "katana" is rare in the sample and common in the reactions, so it must outrank "woman".
    distinctive = [tag["name"] for tag in summary["distinctiveTags"]]
    assert distinctive and distinctive[0] == "katana", summary["distinctiveTags"]

    # The comparison sample accumulates instead of being replaced, and a sync that sees
    # the same images again adds nothing rather than counting them twice.
    assert summary["baselineImages"] == 10, summary["baselineImages"]
    before_weight = store.tag_weights()["katana"]
    run_sync(store, [first, second], following=[11, 77], baseline=baseline,
             candidates={2: katana_pool}, followers={"art_new": 5000, "art_small": 120})
    repeat = store.summary()
    assert repeat["baselineImages"] == 10, repeat["baselineImages"]
    assert store.tag_weights()["katana"] == before_weight, "identical sample changed the weights"
    with store.connect() as db:
        assert db.execute("SELECT image_count FROM tag_baseline WHERE tag_id=1").fetchone()[0] == 10

    # A sample containing genuinely new images grows the pool and the tag counts.
    wider = [[image(200 + n, [], tags=(("woman", 1),)) for n in range(5)]]
    run_sync(store, [first, second], following=[11, 77], baseline=wider,
             candidates={2: katana_pool}, followers={"art_new": 5000, "art_small": 120})
    grown = store.summary()
    assert grown["baselineImages"] == 15, grown["baselineImages"]
    with store.connect() as db:
        assert db.execute("SELECT image_count FROM tag_baseline WHERE tag_id=1").fetchone()[0] == 15
    # "woman" is now even more ubiquitous in the sample, so katana's lead should widen.
    assert store.tag_weights()["katana"] > before_weight * 0.9

    summary = store.summary()

    # Taste signals still drive the personalised ordering, so they must stay correct.
    assert [tag["name"] for tag in store.signal_tags()] == ["katana"], store.signal_tags()
    # Someone whose work the user reacted to is a valid follow target; nobody else is.
    assert store.has_creator(11) and not store.has_creator(21)

    # A reaction removed on Civitai disappears; a changed one is not duplicated.
    changed = [image(1, ["Heart"]), image(2, ["Heart"], creator=("art_two", 12))]
    run_sync(store, [changed], following=[11, 77], baseline=baseline)
    after = store.summary()
    assert after["reactedImages"] == 2, after
    assert after["reactionRecords"] == 2, after
    assert {entry["reaction"]: entry["count"] for entry in after["reactionMix"]}["Heart"] == 2
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reacted_tags WHERE image_id IN (3,4,5)").fetchone()[0] == 0

    # Following from the dashboard updates the derived panels without a resync, and only
    # creators the user actually reacted to are valid follow targets.
    # Only images 1 and 2 survived reconciliation, so art_one (11) and art_two (12) remain.
    assert store.has_creator(12) and not store.has_creator(4321) and not store.has_creator(0)
    # art_three was retired with image 4, so it is no longer a valid follow target.
    assert not store.has_creator(13)
    assert after["creatorsNotFollowed"] == 0, after  # art_two: one reaction, below the bar
    store.set_following(12, True)
    followed_now = store.summary()
    assert followed_now["followedCreators"] == 3, followed_now
    assert followed_now["creatorsNotFollowed"] == 0, followed_now
    assert followed_now["reactedNotFollowed"] == [], followed_now
    store.set_following(12, False)
    assert store.summary()["creatorsNotFollowed"] == 0

    # A stopped sync keeps what it read and never retires unseen rows.
    def slow_page(self, *, cursor=None, limit=100, reactions=None, with_tags=True):
        store.stop_sync()
        return {"items": [image(6, ["Like"])], "nextCursor": "more"}

    taste.SocialClient.images_page = slow_page
    store.start_sync()
    deadline = time.monotonic() + 20
    while store.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert store.status()["phase"] == "stopped", store.status()
    assert store.summary()["reactedImages"] == 3, store.summary()

    # A different account may not read the first account's discovery data.
    store._account = OTHER_ACCOUNT
    other_image = image(7, ["Like"], creator=("art_four", 14))
    other_image["reactions"][0]["userId"] = OTHER_ACCOUNT
    run_sync(store, [[other_image]], following=[14], baseline=baseline)
    switched = store.summary()
    assert switched["reactedImages"] == 1, switched
    assert switched["creatorsNotFollowed"] == 0, switched

    store.reset()
    assert store.summary()["hasData"] is False

print({"rounding": "exact", "reconciled": True, "stopKeepsProgress": True,
       "accountIsolated": True, "reset": True, "signalTags": True,
       "baselineAccumulates": True, "baselineDedupes": True})

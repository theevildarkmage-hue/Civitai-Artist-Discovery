"""Daily gallery views order and filter the whole day, not just the loaded cards."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix="gallery-views-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import server
    from discovery.history import HistoryArchive

    day = (datetime.now() - timedelta(days=1)).date().isoformat()
    history = HistoryArchive(Path(temporary) / "history")
    server.HISTORY = history

    # Rank order deliberately buries the followed creator, as engagement ranking does.
    creators = [("Popular", 300), ("Mid", 40), ("Followed_One", 9), ("Reacted_One", 5),
                ("Me", 3), ("Unknown_A", 2), ("Followed_Two", 1)]
    items, image_id = [], 1
    for index, (name, reactions) in enumerate(creators):
        items.append({"id": image_id, "postId": image_id, "username": name,
                      "createdAt": f"{day}T{12 + index // 4:02d}:00:00Z",
                      "url": "https://image.civitai.com/x.jpeg", "width": 8, "height": 8,
                      "type": "image", "nsfwLevel": "None",
                      "stats": {"reactionCount": reactions}})
        image_id += 1
    history._upsert_normalized(items, forced_date=day)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)",
                   (day, datetime.now().isoformat()))
    history.build_artist_index(day)

    ranked = [row["key"] for row in history.day_artist_keys(day)]
    assert ranked[0] == "popular", ranked
    assert ranked.index("followed_two") > ranked.index("popular"), ranked

    signals = {"followed": {"followed_one", "followed_two"},
               "reacted": {"reacted_one": 4}}

    # Default view is untouched: no ordering is computed at all.
    assert server.day_view_order(day, "discovery", "Me", signals) == (None, None)
    # An unknown view falls back to the default rather than erroring.
    assert server.day_view_order(day, "nonsense", "Me", signals) == (None, None)
    # With no personal signal there is nothing to reorder by.
    assert server.day_view_order(day, "followed", "Me",
                                 {"followed": set(), "reacted": {}}) == (None, None)

    order, total = server.day_view_order(day, "followed", "Me", signals)
    assert total == len(creators), total
    assert order[0] == "me", order
    assert set(order[1:3]) == {"followed_one", "followed_two"}, order
    assert order[3] == "reacted_one", order
    # Rank order still decides inside a tier: Popular outranks Mid among the leftovers.
    assert order[4:] == ["popular", "mid", "unknown_a"], order

    order, total = server.day_view_order(day, "new", "Me", signals)
    assert total == 3, (order, total)
    # Followed, already-reacted, and the user's own card are all absent.
    assert order == ["popular", "mid", "unknown_a"], order
    for gone in ("followed_one", "followed_two", "reacted_one", "me"):
        assert gone not in order, (gone, order)

    # Paging follows the computed order rather than the archive's own.
    page = history.artists_page(day, 0, 2, "Me", order=order)
    assert [item["username"] for item in page] == ["Popular", "Mid"], page
    second = history.artists_page(day, 2, 2, "Me", order=order)
    assert [item["username"] for item in second] == ["Unknown_A"], second
    assert history.artists_page(day, 99, 3, "Me", order=order) == []

    # The default path is unchanged.
    default_page = history.artists_page(day, 0, 3, "Me")
    assert [item["username"] for item in default_page] == ["Me", "Popular", "Mid"], default_page

    # Emerging first: known small accounts lead, ordered by follower count. Creators whose
    # count is unknown sort last rather than being presented as small.
    class FakeTaste:
        def follower_counts(self, names):
            return {"popular": 40000, "mid": 120, "followed_one": 5, "unknown_a": 990}
    real_taste = server.TASTE
    server.TASTE = FakeTaste()
    try:
        order, total = server.day_view_order(day, "emerging", "Me", signals)
    finally:
        server.TASTE = real_taste
    # Emerging is for finding creators you do not have yet, so anyone already followed and
    # your own card are removed outright rather than ranked lower.
    assert total == 4, (order, total)
    for gone in ("followed_one", "followed_two", "me"):
        assert gone not in order, (gone, order)
    # Under the threshold first, ordered by the day's engagement rank rather than by
    # ascending follower count, so a one-follower account does not lead the gallery.
    assert order[:2] == ["mid", "unknown_a"], order
    assert order[2] == "popular", order
    # The creator with no known count trails the one with 40,000 followers.
    assert order[3] == "reacted_one", order

    # For you: highest taste score first, unscored creators behind everyone scored.
    reps = {row["key"]: row["representativeId"] for row in history.day_artist_keys(day)}

    class FakeScores:
        def score_images(self, ids):
            wanted = {reps["mid"]: 2.4, reps["unknown_a"]: 3.1, reps["popular"]: 0.5}
            return {k: v for k, v in wanted.items() if k in set(ids)}
    real_taste = server.TASTE
    server.TASTE = FakeScores()
    try:
        order, total = server.day_view_order(day, "foryou", "Me", signals)
    finally:
        server.TASTE = real_taste
    assert total == len(creators), total
    assert order[:3] == ["unknown_a", "mid", "popular"], order
    assert set(order[3:]) == {"me", "followed_one", "reacted_one", "followed_two"}, order

    # With nothing scored yet the view falls back to the archive order rather than
    # presenting an arbitrary one.
    class NoScores:
        def score_images(self, ids):
            return {}
    server.TASTE = NoScores()
    try:
        assert server.day_view_order(day, "foryou", "Me", signals) == (None, None)
    finally:
        server.TASTE = real_taste

    decorated = server.decorate_history_artist({"username": "Unknown_A"}, {}, set(), signals)

print({"defaultUnchanged": True, "followedTiers": ["self", "followed", "reacted", "rest"],
       "newToYouExcludesKnownAndSelf": 3, "pagingFollowsView": True, "forYouScored": True, "forYouFallsBack": True,
       "emergingExcludesFollowed": True})

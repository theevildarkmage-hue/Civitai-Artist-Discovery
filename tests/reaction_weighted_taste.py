"""Hearts outweigh Likes and Dislike-only images never train recommendations."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery.taste import TasteStore


with tempfile.TemporaryDirectory(prefix="reaction-weighted-taste-") as temporary:
    store = TasteStore(Path(temporary) / "taste")
    with store.connect() as db:
        reacted = []
        tags = []
        reactions = []
        for start, creator, tag, reaction in (
                (1, "HeartArtist", "heart-tag", "Heart"),
                (11, "LikeArtist", "like-tag", "Like"),
                (21, "DislikedArtist", "disliked-tag", "Dislike")):
            for image_id in range(start, start + 3):
                reacted.append((image_id, creator, "now", "now"))
                tags.append((image_id, image_id, tag))
                reactions.append((image_id, reaction))
        db.executemany("INSERT INTO reacted_images(image_id,creator_username,first_observed_at,last_observed_at) VALUES(?,?,?,?)", reacted)
        db.executemany("INSERT INTO reacted_tags(image_id,tag_id,tag_name) VALUES(?,?,?)", tags)
        db.executemany("INSERT INTO reacted_reactions(image_id,reaction) VALUES(?,?)", reactions)
        db.executemany("INSERT INTO archive_image_tags(image_id,tag_name) VALUES(?,?)",
                       [(101, "heart-tag"), (102, "like-tag"), (103, "disliked-tag")])

    scores = store.score_images([101, 102, 103])
    assert scores[101] > scores[102] > 0, scores
    assert 103 not in scores, scores
    signals = store.gallery_signals()
    assert signals["reacted"] == {"heartartist": 3, "likeartist": 3}, signals

print({"heartStrongerThanLike": True, "dislikeExcludedFromTaste": True,
       "dislikeExcludedFromAffinity": True})

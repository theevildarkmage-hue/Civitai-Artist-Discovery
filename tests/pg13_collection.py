"""Regression: the safe Red default retains PG and PG-13 images, including multi-image posts."""

from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery.history import HistoryArchive


def image(image_id: int, post_id: int, rating: str) -> dict:
    return {
        "id": image_id,
        "postId": post_id,
        "username": "rating-test",
        "createdAt": "2026-07-31T16:54:38.648Z",
        "url": f"https://image.civitai.com/test/{image_id}.jpeg",
        "width": 768,
        "height": 1024,
        "type": "image",
        "nsfwLevel": rating,
        "stats": {"reactionCount": 0},
    }


with tempfile.TemporaryDirectory(prefix="civitai-pg13-") as temporary:
    archive = HistoryArchive(Path(temporary) / "history")
    captured_params = []
    rows = [image(1, 100, "None"), image(2, 200, "Soft"), image(3, 200, "Soft"),
            image(4, 200, "Soft"), image(5, 200, "Soft")]
    # The older crossing row causes the real collector to finish this day.
    rows.append({**image(6, 300, "None"), "createdAt": "2026-07-31T04:59:59.000Z"})

    def request(params, minimum_interval=1.0, on_delay=None, cancel_event=None, on_timing=None,
                on_transfer=None):
        captured_params.append(dict(params))
        return {"items": rows, "metadata": {"nextCursor": "older"}}, 1024

    archive._request = request
    archive.start("2026-07-31", "2026-07-31T05:00:00Z", "2026-08-01T05:00:00Z", "America/Chicago")
    deadline = time.monotonic() + 5
    while archive.status("2026-07-31")["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert archive.status("2026-07-31")["complete"]
    assert {call["browsingLevel"] for call in captured_params} == {1, 2}
    assert "nsfw" not in captured_params[0]
    retained = archive.artist_images("2026-07-31", "rating-test")
    assert [entry["id"] for entry in retained] == [5, 4, 3, 2, 1]
    assert len({entry["postId"] for entry in retained}) == 2

print({"defaultRating": "Soft", "retained": 5, "multiImagePost": 4})

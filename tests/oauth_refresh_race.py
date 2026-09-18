"""Concurrent callers refresh an expired token once, not once per thread.

Civitai rotates refresh tokens, so a second redemption of the same one returns HTTP 400.
The gallery issues several requests at once, so an unsynchronised refresh failed every
request but one -- which reached the browser as cards stuck on the tag-check message.
"""

from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery import oauth


CLIENT = "test-client"
stored = {"access_token": "old", "refresh_token": "first", "client_id": CLIENT,
          "expires_at": int(time.time()) - 10, "identity": {"username": "someone"}}
posts = []
lock = threading.Lock()


def fake_post(name, values):
    # Rotation: only the refresh token currently stored may be redeemed.
    with lock:
        posts.append(values["refresh_token"])
    time.sleep(0.05)  # Widen the window every thread used to pile into.
    if values["refresh_token"] != stored["refresh_token"]:
        raise RuntimeError(f"HTTP 400: refresh token {values['refresh_token']} was already used")
    return {"access_token": f"new-after-{values['refresh_token']}",
            "refresh_token": "second", "expires_in": 3600}


oauth._load = lambda: dict(stored)
oauth._save = lambda tokens: stored.update(tokens)
oauth._post = fake_post
oauth.client_id = lambda: CLIENT

results, failures = [], []


def worker():
    try: results.append(oauth.get_access_token())
    except Exception as error: failures.append(error)


threads = [threading.Thread(target=worker) for _ in range(8)]
for thread in threads: thread.start()
for thread in threads: thread.join()

assert not failures, f"refresh failed for {len(failures)} of 8 callers: {failures[0]}"
assert len(posts) == 1, f"redeemed the refresh token {len(posts)} times, expected 1"
assert set(results) == {"new-after-first"}, results
assert stored["refresh_token"] == "second"

# A caller arriving after the refresh reuses the stored token without another request.
assert oauth.get_access_token() == "new-after-first"
assert len(posts) == 1

print("oauth refresh race: one redemption for 8 concurrent callers")

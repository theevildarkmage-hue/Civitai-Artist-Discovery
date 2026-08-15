"""Signing out must not force a full re-download of the account's reaction history.

Disconnect used to wipe the whole taste analysis unconditionally, so signing back in
re-read every reaction from scratch — expensive for exactly the accounts this app
targets, ones with a lot of reactions. The account-isolation guard
(TasteStore._require_account) already handles the real risk — a *different* account
signing in — more precisely than an unconditional wipe on every sign-out ever did, so
disconnect no longer needs to delete anything itself.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 8901
ACCOUNT = 4242


def image(image_id, creator=("art_one", 11)):
    username, creator_id = creator
    return {"id": image_id, "postId": image_id, "nsfwLevel": 1, "hasMeta": True,
            "createdAt": "2026-08-01T12:00:00Z", "stats": {"reactionCount": 1},
            "user": {"id": creator_id, "username": username},
            "reactions": [{"userId": ACCOUNT, "reaction": "Like"}],
            "tags": [{"id": 1, "name": "portrait", "source": "WD14"}]}


with tempfile.TemporaryDirectory(prefix="civitai-signout-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.taste import TasteStore

    # ---------- Part 1: the same account resyncing after data survives takes the
    # incremental path — asks for tags only on images it has not seen before. ----------
    store = TasteStore(Path(temporary) / "discovery")
    taste.auth_status = lambda: {"id": ACCOUNT, "connected": True}
    taste.MIN_PAUSE = taste.MAX_PAUSE = 0.0
    with_tags_requests = []

    def make_pager(pages):
        queue = list(pages)
        def images_page(self, *, cursor=None, limit=100, reactions=None, with_tags=True,
                        tags=None, period="AllTime"):
            # Baseline sampling calls this same method with reactions=None to read the
            # general Civitai population; only the account's own reacted-images listing
            # is subject to the incremental with_tags optimisation being tested here.
            if reactions:
                with_tags_requests.append(with_tags)
                items = queue.pop(0) if queue else []
                return {"items": items, "nextCursor": "more" if queue else None}
            return {"items": [], "nextCursor": None}
        return images_page

    taste.SocialClient.images_page = make_pager([[image(1), image(2)]])
    taste.SocialClient.query = lambda self, procedure, payload: []
    taste.SocialClient.batch_query_optional = lambda self, procedure, payloads: []
    store.start_sync()
    deadline = time.monotonic() + 20
    while store.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not store.status()["error"], store.status()
    assert store.summary()["reactedImages"] == 2, store.summary()
    # A first sync has nothing to reuse, so it fetches tags inline.
    assert with_tags_requests == [True], with_tags_requests

    # "Sign out, sign back in" no longer touches the store at all — simulated here simply
    # by not resetting it — then a second sync arrives with one genuinely new image.
    with_tags_requests.clear()
    taste.SocialClient.images_page = make_pager([[image(1), image(2), image(3)]])
    store.start_sync()
    deadline = time.monotonic() + 20
    while store.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not store.status()["error"], store.status()
    assert store.summary()["reactedImages"] == 3, store.summary()
    # The bulk listing page must not re-request tags for images already known.
    assert with_tags_requests == [False], with_tags_requests

    # A different connected account must never inherit the previous account's summary,
    # even before a background sync has had a chance to start. This is a read-boundary
    # privacy check, not merely a sync-time cleanup check.
    isolation = TasteStore(Path(temporary) / "account-isolation")
    isolation._store_page([image(99)], 1, ACCOUNT)
    with isolation.connect() as db:
        isolation._set_state(db, "account_id", ACCOUNT)
        isolation._set_state(db, "last_sync_at", "2026-08-01T12:00:00+00:00")
        isolation._set_state(db, "baseline_images", 999)
    taste.auth_status = lambda: {"id": ACCOUNT + 1, "connected": True}
    isolated = isolation.summary()
    assert isolated["hasData"] is False and isolated["reactedImages"] == 0, isolated
    assert isolated.get("lastSyncAt") is None, isolated
    taste.auth_status = lambda: {"id": ACCOUNT, "connected": True}

    # ---------- Part 2: the live server's disconnect endpoint no longer deletes the
    # taste analysis. ----------
    import discovery.oauth as oauth
    payload = {"access_token": "a", "refresh_token": "r", "expires_at": 2 ** 31,
               "client_id": oauth.client_id(), "scope": 524321,
               "identity": {"id": ACCOUNT, "username": "tester"}}
    oauth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    oauth.TOKEN_PATH.write_bytes(oauth._crypt(json.dumps(payload).encode(), True))

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT),
                                "--no-browser"], cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=15) as response:
                return json.loads(response.read())

        def post(path, body):
            request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())

        before = get("/api/discovery/summary")
        assert before["hasData"] is True and before["reactedImages"] == 3, before

        disconnected = post("/api/oauth/disconnect", {})
        assert disconnected == {"connected": False}, disconnected

        after = get("/api/discovery/summary")
        assert after["hasData"] is True, "sign-out deleted the taste analysis"
        assert after["reactedImages"] == 3, after

        # The token really is gone — this is a genuine sign-out, not a no-op.
        status = get("/api/auth-status")
        assert status.get("connected") is not True, status

        # ---------- Part 3: signing back in with data already present skips the full
        # "getting to know your taste" screen and refreshes quietly instead. ----------
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"connected":false,"socialWrite":false}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector("#welcome:not(.hidden)", timeout=15000)

            sync_calls = []
            page.on("request", lambda request: sync_calls.append(request.url)
                    if "/api/discovery/sync" in request.url and request.method == "POST" else None)
            page.route("**/api/oauth/login", lambda route: route.fulfill(status=202,
                content_type="application/json", body='{"state":"loading"}'))
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":true,"username":"tester","id":4242,'
                     '"oauthJob":{"state":"complete"}}'))

            page.click("#welcomeConnect")
            page.wait_for_selector("#buildSetup:not(.hidden), #daySegment:visible", timeout=20000)

            # The heavy first-time treatment never appeared — "only happens once" would
            # have been a lie the second time.
            assert page.inner_text("#welcomeTitle") != "Getting to know your taste", \
                "returning sign-in showed the first-time onboarding screen"
            assert sync_calls, "returning sign-in never refreshed the existing analysis"
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

print({"sameAccountResyncIsIncremental": True, "disconnectPreservesAnalysis": True,
       "disconnectStillEndsTheSession": True, "reconnectSkipsFullOnboarding": True,
       "reconnectRefreshesQuietly": True, "differentAccountIsolatedBeforeSync": True})

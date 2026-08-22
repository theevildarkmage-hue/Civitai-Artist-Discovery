"""Account-isolation and browser tests for read-only creator enrichment."""

from __future__ import annotations

from datetime import datetime, timedelta
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
import server
from discovery.history import HistoryArchive


with tempfile.TemporaryDirectory(prefix="creator-metadata-unit-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    real_taste = server.TASTE
    server.CREATOR_PROFILES = root / "creator_profiles.json"
    server.FOLLOW_CACHE = root / "following.json"
    server.CREATOR_PROFILES.write_text(json.dumps({"byUsername": {"blueeyes": {
        "id": 482053, "username": "Blueeyes", "profilePicture": {"url": "avatar-uuid", "name": "avatar.png"},
        "stats": {"followerCountAllTime": 640}
    }, "bigname": {"id": 900, "username": "BigName", "stats": {"followerCountAllTime": 42000}},
       "unknowncount": {"id": 901, "username": "UnknownCount"}}}), encoding="utf-8")

    class FakeSocialClient:
        calls: list[tuple[str, dict]] = []
        def query(self, procedure: str, payload: dict):
            self.calls.append((procedure, payload))
            if procedure == "user.getFollowingUsers":
                return [482053] if server.auth_status()["id"] == 111 else []
            raise AssertionError(f"Unexpected profile lookup: {procedure}")
        def batch_query_optional(self, procedure: str, payloads: list[dict]):
            self.calls.append((procedure, {"payloads": payloads}))
            values = {"resolvedlater": {"id": 902, "username": "ResolvedLater",
                       "stats": {"followerCountAllTime": 1500}}}
            return [values.get(payload["username"].casefold()) for payload in payloads]
        def mutate(self, procedure: str, payload: dict):
            raise AssertionError("Read-only enrichment attempted a mutation")

    class FakeTaste:
        counts: dict[str, int] = {}
        def follower_counts(self, names):
            return {name.casefold(): self.counts[name.casefold()] for name in names
                    if name.casefold() in self.counts}

    server.SocialClient = FakeSocialClient
    server.TASTE = FakeTaste()
    server.auth_status = lambda: {"connected": True, "id": 111, "username": "account-one", "socialWrite": False}
    first = server.enrich_creator_metadata(["Blueeyes"])["blueeyes"]
    cache = json.loads(server.FOLLOW_CACHE.read_text(encoding="utf-8"))
    assert first["following"] is True and cache["userId"] == 111
    assert server.followed_usernames() == {"blueeyes"}

    server.auth_status = lambda: {"connected": True, "id": 222, "username": "account-two", "socialWrite": False}
    assert server.followed_usernames() == set(), "Previous account follow state leaked before refresh"
    second = server.enrich_creator_metadata(["Blueeyes"])["blueeyes"]
    cache = json.loads(server.FOLLOW_CACHE.read_text(encoding="utf-8"))
    assert second["following"] is False and cache["userId"] == 222
    assert all(name == "user.getFollowingUsers" for name, _ in FakeSocialClient.calls)

    # Avatars hosted outside Civitai are blocked by the page CSP, so they are dropped here
    # and the card renders initials instead of logging a blocked-resource error.
    assert server.profile_avatar({"profilePicture": {"url": "https://lh3.googleusercontent.com/a/x=s96-c"}}) is None
    assert server.profile_avatar({"profilePicture": {"url": "https://evil.example.com/civitai.com/a.png"}}) is None
    assert server.profile_avatar({"profilePicture": {"url": "https://image.civitai.com/a/b.png"}}) == "https://image.civitai.com/a/b.png"
    relative = server.profile_avatar({"profilePicture": {"url": "abc-uuid", "name": "a.png"}})
    assert relative.startswith("https://image.civitai.com/"), relative

    # Follower counts ride along on the cached profile, so no extra lookup is made.
    before = len(FakeSocialClient.calls)
    enriched = server.enrich_creator_metadata(["Blueeyes", "BigName", "UnknownCount"])
    assert enriched["blueeyes"]["followers"] == 640 and enriched["blueeyes"]["emerging"] is True
    assert enriched["bigname"]["followers"] == 42000 and enriched["bigname"]["emerging"] is False
    # A profile without stats is unknown, never zero followers and never "emerging".
    assert enriched["unknowncount"]["followers"] is None
    assert enriched["unknowncount"]["emerging"] is False
    assert all(name == "user.getFollowingUsers"
               for name, _ in FakeSocialClient.calls[before:]), FakeSocialClient.calls[before:]

    # The full-day sweep stores follower counts in SQLite rather than in the visible-card
    # profile JSON. Card metadata uses that value immediately, without another lookup.
    server.TASTE.counts = {"unknowncount": 77, "gonecreator": 12}
    swept = server.enrich_creator_metadata(["UnknownCount"])["unknowncount"]
    assert swept["followers"] == 77 and swept["emerging"] is True

    # A deleted creator is optional: it does not discard the successful profiles beside
    # it, and a swept count remains usable even when no live profile can be resolved.
    mixed = server.enrich_creator_metadata(["ResolvedLater", "GoneCreator"])
    assert mixed["resolvedlater"]["followers"] == 1500
    assert mixed["gonecreator"]["followers"] == 12
    assert mixed["gonecreator"]["avatarUrl"] is None
    server.TASTE = real_taste


PORT = 8881
with tempfile.TemporaryDirectory(prefix="creator-metadata-browser-", ignore_cleanup_errors=True) as temporary:
    root = Path(temporary)
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    history = HistoryArchive(root / "history")
    artwork = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='768' height='900'%3E%3Crect width='100%25' height='100%25' fill='%232b5360'/%3E%3C/svg%3E"
    history._upsert_normalized([{"id": 1001, "postId": 1001, "username": "Blueeyes",
        "createdAt": f"{yesterday}T12:00:00Z", "url": artwork, "width": 768, "height": 900,
        "type": "image", "nsfwLevel": "None", "baseModel": "Test", "stats": {"reactionCount": 3}}], forced_date=yesterday)
    with history.connect() as database:
        database.execute("INSERT OR REPLACE INTO days(day,complete,updated_at) VALUES(?,1,?)", (yesterday, datetime.now().isoformat()))
    history.build_artist_index(yesterday)
    environment = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT), "--no-browser"], cwd=ROOT, env=environment)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read(); break
            except Exception:
                if time.monotonic() > deadline: raise
                time.sleep(.2)
        state = {"connected": True, "metadataCalls": 0, "writes": 0}
        avatar = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='96'%3E%3Ccircle cx='48' cy='48' r='48' fill='%2355d6c2'/%3E%3C/svg%3E"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"connected": state["connected"], "id": 111, "username": "read-only-test", "socialWrite": False, "oauthJob": {"state": "complete" if state["connected"] else "idle"}})))
            def login(route):
                state["connected"] = True
                route.fulfill(status=202, content_type="application/json", body='{"state":"complete"}')
            page.route("**/api/oauth/login", login)
            def metadata(route):
                state["metadataCalls"] += 1
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"creators": {"blueeyes": {"username": "Blueeyes", "avatarUrl": avatar, "following": True, "userId": 482053, "followers": 640, "emerging": True}}}))
            page.route("**/api/creator-metadata**", metadata)
            def disconnect(route):
                state["connected"] = False
                route.fulfill(status=200, content_type="application/json", body='{"connected":false}')
            page.route("**/api/oauth/disconnect", disconnect)
            page.route("**/api/follow", lambda route: (state.__setitem__("writes", state["writes"] + 1), route.fulfill(status=500, body="unexpected")))
            page.route("**/api/discovery/summary", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"hasData":true}'))
            page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector(".creator-card")
            card = page.locator(".creator-card").first
            # Public profile data is fetched once and painted onto the card.
            card.locator("img.creator-avatar").wait_for(timeout=10000)
            card.locator(".follow-button.is-following").wait_for()
            # Civitai withheld write access here, so the control is visible but inert and
            # no follow request may leave the app.
            assert card.locator(".follow-button").is_disabled()
            assert state["metadataCalls"] == 1 and state["writes"] == 0
            assert card.locator(".creator-followers").text_content().strip() == "· 640 followers"
            assert card.locator(".creator-badge").text_content() == "EMERGING"
            assert "emerging" in card.locator(".creator-badge").get_attribute("class")
            # The follower text lives in its own node beside the date, because the carousel
            # rewrites the date on every image and would otherwise erase it.
            assert card.locator(".image-age").text_content() != ""
            assert card.evaluate("el => el.querySelector('.image-age')"
                                 ".contains(el.querySelector('.creator-followers'))") is False
            # The username still resolves alone, which the metadata batch depends on.
            assert card.locator(".creator-identity strong").text_content() == "Blueeyes"
            card.evaluate("element => element.applyCreatorMetadata({avatarUrl:'/missing-avatar.png', following:false, userId:482053})")
            card.locator(".creator-avatar.fallback").wait_for(timeout=10000)
            assert state["writes"] == 0

            # Signing out returns to the front door: there is no signed-out gallery.
            page.locator("#disconnect").click()
            page.wait_for_selector("#welcome:not(.hidden)", timeout=15000)
            assert page.is_hidden("#gallery"), "a gallery was left on screen after signing out"
            assert state["writes"] == 0
            browser.close()
        print(json.dumps({"accountIsolation": True, "visibleCardsRefresh": True, "blueeyesFollowing": True,
            "writesWithoutGrant": state["writes"], "brokenAvatarFallback": True, "metadataCalls": state["metadataCalls"]}))
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill()

"""The discovery tab renders real numbers, stays responsive, and shows no artwork."""

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
PORT = 8887
SHOTS = ROOT / "reports" / "discovery-dashboard"

with tempfile.TemporaryDirectory(prefix="civitai-discovery-ui-", ignore_cleanup_errors=True) as temporary:
    os.environ["CIVITAI_HISTORY_DATA_DIR"] = temporary
    import discovery.taste as taste
    from discovery.history import HistoryArchive
    from discovery.taste import TasteStore

    # A completed day so the gallery tab has something to switch back to.
    history = HistoryArchive(Path(temporary) / "history")
    value = (datetime.now() - timedelta(days=1)).date().isoformat()
    pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='768' height='900'%3E%3C/svg%3E"
    history._upsert_normalized([{"id": 9001, "postId": 9001, "username": "GalleryArtist",
        "createdAt": f"{value}T13:00:00Z", "url": pixel, "width": 768, "height": 900,
        "type": "image", "nsfwLevel": "None", "stats": {"reactionCount": 1}}], forced_date=value)
    with history.connect() as db:
        db.execute("INSERT INTO days(day,complete,updated_at) VALUES(?,1,?)", (value, datetime.now().isoformat()))
    history.build_artist_index(value)

    # Seed the discovery store through its normal write path.
    store = TasteStore(Path(temporary) / "discovery")
    taste.auth_status = lambda: {"id": 4242, "connected": True}
    taste.MIN_PAUSE = taste.MAX_PAUSE = 0.0
    creators = [("Bone_Of_Moon", 11, 40, True), ("SeeSeeLP", 12, 25, True),
                ("GWGeek", 13, 12, False), ("AiMetatron", 14, 8, False)]
    rare = ("katana", 2)
    items, image_id = [], 1
    for username, creator_id, count, _ in creators:
        for index in range(count):
            tags = [("woman", 1)] + ([rare] if index % 2 == 0 else [])
            items.append({"id": image_id, "postId": image_id, "nsfwLevel": 1, "hasMeta": True,
                "createdAt": f"{value}T12:00:00Z", "stats": {"reactionCount": 2},
                "user": {"id": creator_id, "username": username},
                "reactions": [{"userId": 4242, "reaction": "Heart" if index % 3 == 0 else "Like"}],
                "tags": [{"id": tag_id, "name": name, "source": "WD14"} for name, tag_id in tags]})
            image_id += 1
    # "woman" saturates the comparison sample; "katana" is rare in it, so only it earns lift.
    sample = [{"id": 5000 + n, "user": {"id": 99, "username": "Sampled"}, "reactions": [],
               "tags": [{"id": 1, "name": "woman", "source": "WD14"}]
                       + ([{"id": 2, "name": "katana", "source": "WD14"}] if n == 0 else [])}
              for n in range(20)]

    # Candidates for the "katana" signal: a followed creator, an already-reacted creator,
    # and two genuinely new ones, only the second of which is emerging.
    candidates = [
        {"id": 6000, "user": {"id": 11, "username": "Bone_Of_Moon"}, "reactions": [], "tags": []},
        {"id": 6001, "user": {"id": 13, "username": "GWGeek"}, "reactions": [], "tags": []},
        {"id": 6002, "user": {"id": 31, "username": "NewBigName"}, "reactions": [], "tags": []},
        {"id": 6003, "user": {"id": 31, "username": "NewBigName"}, "reactions": [], "tags": []},
        {"id": 6004, "user": {"id": 32, "username": "TinyArtist"}, "reactions": [], "tags": []},
    ]
    follower_counts = {"NewBigName": 8200, "TinyArtist": 140,
                       "GWGeek": 430, "AiMetatron": 5100, "Bone_Of_Moon": 3025}

    def images_page(self, *, cursor=None, limit=100, reactions=None, with_tags=True,
                    tags=None, period="AllTime"):
        if tags:
            return {"items": candidates, "nextCursor": None}
        return {"items": items if reactions else sample, "nextCursor": None}

    taste.SocialClient.images_page = images_page
    taste.SocialClient.query = lambda self, procedure, payload: [11, 12, 77]
    taste.SocialClient.batch_query_optional = lambda self, procedure, payloads: [
        {"username": p["username"], "stats": {"followerCountAllTime": follower_counts.get(p["username"])}}
        for p in payloads]
    store.start_sync()
    deadline = time.monotonic() + 20
    while store.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not store.status()["error"], store.status()
    summary = store.summary()
    assert summary["reactedImages"] == 85, summary
    assert summary["followedCreators"] == 3 and summary["creatorsNotFollowed"] == 2, summary

    env = {**os.environ, "CIVITAI_HISTORY_DATA_DIR": temporary}
    process = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(PORT), "--no-browser"],
                               cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/history/config", timeout=1).read()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.2)

        SHOTS.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Signed in, but Civitai withheld write access: the dashboard must render its
            # analysis in full and still offer no way to write.
            page.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"socialWrite":false,"scopeGrantsWrite":false,'
                     '"username":"tester","id":4242}'))
            # This server has no real Civitai session, so the account-backed reads are
            # stubbed; they are not what this test is about.
            page.route("**/api/creator-metadata**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"creators":{}}'))
            page.route("**/api/reaction-status**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"images":{}}'))
            page.route("**/api/history/prepare**", lambda route: route.fulfill(status=200,
                content_type="application/json", body='{"prepared":false}'))
            auth_calls = []
            page.on("request", lambda request: auth_calls.append(request.url)
                    if "/api/auth-status" in request.url else None)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

            before = len(auth_calls)
            page.click("#tabDiscovery")
            # Opening the tab must re-read connection state; the sync button depends on it
            # and nothing else refreshes it until a completed day loads.
            page.wait_for_timeout(600)
            assert len(auth_calls) > before, auth_calls
            page.wait_for_selector("#summaryRow .metric-card")
            values = page.eval_on_selector_all("#summaryRow .metric-card .value",
                                               "nodes => nodes.map(n => n.textContent)")
            assert values == ["3", "85", "4", "2"], values

            mix = page.eval_on_selector_all(".donut-legend-value", "nodes => nodes.map(n => n.textContent)")
            percents = [float(text.split("·")[1].strip().rstrip("%")) for text in mix]
            assert abs(sum(percents) - 100.0) < 0.001, mix
            # The legend names every reaction including the zeroes, so identity and value
            # never rest on the arcs alone.
            names = page.eval_on_selector_all(".donut-legend-name", "nodes => nodes.map(n => n.textContent)")
            assert names == ["Like", "Heart", "Laugh", "Cry"], names
            # Only non-zero reactions are drawn, and the arcs must actually paint.
            arcs = page.eval_on_selector_all(".donut-arc",
                "nodes => nodes.map(n => ({d: n.getAttribute('stroke-dasharray'), w: n.getBoundingClientRect().width}))")
            assert len(arcs) == 2, arcs
            assert all(arc["w"] > 0 and float(arc["d"].split()[0]) > 0 for arc in arcs), arcs
            assert float(arcs[0]["d"].split()[0]) > float(arcs[1]["d"].split()[0]), arcs
            assert page.text_content(".donut-centre b") == "85"

            # The donut shares the top row with the headline cards instead of starting a
            # panel row of its own, which is what pushed later panels down the page.
            tops = page.eval_on_selector_all("#summaryRow .metric-card",
                "nodes => nodes.map(n => Math.round(n.getBoundingClientRect().top))")
            assert len(tops) == 5 and len(set(tops)) == 1, tops

            distinctive = page.eval_on_selector_all("#distinctiveTags .rank-name",
                                                    "nodes => nodes.map(n => n.textContent)")
            assert distinctive and distinctive[0] == "katana", distinctive

            not_followed = page.eval_on_selector_all("#notFollowed .rank-name a",
                                                     "nodes => nodes.map(n => n.textContent.trim())")
            assert not_followed == ["@GWGeek", "@AiMetatron"], not_followed
            # Follower counts and the emerging badge appear on every creator panel.
            worth = page.eval_on_selector_all("#notFollowed .rank-item", """nodes => nodes.map(n => ({
                name: n.querySelector('.rank-name a').textContent,
                value: n.querySelector('.rank-value').textContent,
                emerging: !!n.querySelector('.pill.emerging')}))""")
            assert worth[0]["value"] == "12 images · 430 followers", worth
            assert worth[0]["emerging"] is True and worth[1]["emerging"] is False, worth
            assert worth[1]["value"] == "8 images · 5,100 followers", worth
            top_creator = page.eval_on_selector("#topCreators .rank-item .rank-value",
                                                "n => n.textContent")
            assert top_creator == "40 images · 3,025 followers", top_creator
            # Nothing in a creator row may be visually clipped: badges and counts must fit,
            # and only the name is allowed to ellipsise.
            clipped = page.evaluate("""() => {
                const bad = [];
                document.querySelectorAll('#notFollowed .rank-item, #topCreators .rank-item, '
                    + '#recommended .rank-item').forEach(row => {
                    row.querySelectorAll('.pill, .rank-value, .follow-button').forEach(el => {
                        if (el.scrollWidth > el.clientWidth + 1) bad.push(el.textContent.trim());
                    });
                    const r = row.getBoundingClientRect(), p = row.parentElement.getBoundingClientRect();
                    if (r.right > p.right + 1) bad.push('row overflows: ' + row.textContent.trim());
                });
                return bad;
            }""")
            assert clipped == [], clipped

            # The most actionable list leads the panels; it is the whole point of the app.
            order = page.evaluate("""() => {
                const top = id => Math.round(document.getElementById(id)
                    .closest('.panel').getBoundingClientRect().top);
                return {lead: top('notFollowed'), distinctive: top('distinctiveTags'),
                        creators: top('topCreators')};
            }""")
            assert order["lead"] < order["distinctive"] and order["lead"] < order["creators"], order
            assert "of 2" in page.text_content("#notFollowedNote"), page.text_content("#notFollowedNote")
            # Read-only: every follow control is present but refuses, and no write is sent.
            writes = []
            page.on("request", lambda r: writes.append(r.url) if r.method == "POST"
                    and "/api/follow" in r.url else None)
            follow_state = page.eval_on_selector_all("#notFollowed .follow-button",
                "nodes => nodes.map(n => ({disabled: n.disabled, text: n.textContent}))")
            assert len(follow_state) == 2, follow_state
            assert all(item["disabled"] and item["text"] == "+ Follow" for item in follow_state), follow_state
            page.eval_on_selector("#notFollowed .follow-button", "n => n.click()")
            page.wait_for_timeout(400)
            assert writes == [], writes
            assert page.eval_on_selector_all("#topCreators .pill:not(.emerging)",
                                             "nodes => nodes.length") == 2

            # Suggestions were moved into the For You feed, so the dashboard must not
            # carry them any more.
            assert page.evaluate("!!document.getElementById('recommendedPanel')") is False
            assert page.eval_on_selector_all("#recommended .rank-item", "n => n.length") == 0

            # With social actions enabled, following from the dashboard sends exactly one
            # write for the clicked creator and updates the headline counts in place.
            # Runs before the reset below, which empties the store.
            writer = browser.new_page(viewport={"width": 1440, "height": 900})
            writer_errors = []
            writer.on("console", lambda m: writer_errors.append(m.text) if m.type == "error" else None)
            writer.on("pageerror", lambda error: writer_errors.append(str(error)))
            writer.route("**/api/auth-status", lambda route: route.fulfill(status=200,
                content_type="application/json",
                body='{"connected":true,"username":"Tester","socialWrite":true}'))
            sent = []

            def follow_route(route):
                sent.append(json.loads(route.request.post_data))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"userId": sent[-1]["userId"], "following": True,
                                               "changed": True}))
            writer.route("**/api/follow", follow_route)
            writer.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            writer.click("#tabDiscovery")
            writer.wait_for_selector("#notFollowed .follow-button:not([disabled])")
            # Wait on the actual network round trip, not on the DOM update it causes —
            # racing a fixed timeout against "however long the click-to-response-to-DOM
            # chain takes" was the flaky part, however large the timeout. Confirming the
            # response first turns that guess into a real signal.
            with writer.expect_response("**/api/follow", timeout=30000):
                writer.eval_on_selector("#notFollowed .follow-button", "n => n.click()")
            writer.wait_for_selector("#notFollowed .follow-button.is-following", timeout=5000)
            assert sent == [{"userId": 13, "username": "GWGeek", "following": True}], sent
            assert writer.text_content("#notFollowed .follow-button") == "✓ Following"
            # The count is re-read from the server rather than guessed in the browser. The
            # store is untouched here because /api/follow is mocked, so it stays at 2.
            assert writer.eval_on_selector_all("#summaryRow .metric-card .value",
                "nodes => nodes.map(n => n.textContent)")[3] == "2"
            # This page claims a connection the server does not have, so creator enrichment
            # is correctly refused. That must be a clean 401, never a 500, and never a
            # JavaScript error.
            assert all("401 (Unauthorized)" in message for message in writer_errors), writer_errors
            writer.close()

            # A statistics view must never render artwork, and must not need write access.
            assert page.eval_on_selector_all("#discovery img", "nodes => nodes.length") == 0
            assert page.is_hidden("#daySegment") and page.is_hidden("#olderDay")

            for width, height, name in ((1440, 900, "wide"), (900, 900, "medium"), (430, 850, "narrow")):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(200)
                overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
                assert not overflow, f"horizontal overflow at {width}px"
                page.screenshot(path=str(SHOTS / f"discovery-{name}.png"), full_page=True)

            page.set_viewport_size({"width": 1440, "height": 900})
            page.click("#tabGallery")
            page.wait_for_selector(".creator-card")
            assert page.is_hidden("#discovery") and page.is_visible("#daySegment")
            page.screenshot(path=str(SHOTS / "gallery-after-return.png"))

            # Artwork must not be requested for cards nowhere near the viewport. Setting
            # src while a card is detached defeats loading="lazy" and made a page of cards
            # request every preview at once.
            offscreen = page.evaluate("""() => {
                const cards = [...document.querySelectorAll('.creator-card')];
                return cards.filter(c => c.getBoundingClientRect().top > window.innerHeight + 1500)
                            .filter(c => c.querySelector('.image-button img').src).length;
            }""")
            assert offscreen == 0, f"{offscreen} far-offscreen cards already requested artwork"
            onscreen = page.evaluate("""() => [...document.querySelectorAll('.creator-card')]
                .filter(c => c.getBoundingClientRect().top < window.innerHeight)
                .every(c => c.querySelector('.image-button img').src)""")
            assert onscreen, "a visible card has no artwork attached"

            # Switching views repeatedly must not duplicate cards or empty the gallery: a
            # page already in flight carries a stale offset for the previous ordering.
            for view in ("foryou", "discovery", "new", "discovery"):
                page.select_option("#dayView", view)
                page.wait_for_timeout(120)
            page.wait_for_timeout(2500)
            shown = page.eval_on_selector_all(".creator-card .creator-identity strong",
                                              "n => n.map(x => x.textContent)")
            assert shown, "view switching emptied the gallery"
            assert len(shown) == len(set(shown)), sorted(shown)

            page.click("#tabDiscovery")
            page.on("dialog", lambda dialog: dialog.accept())
            page.click("#resetDiscovery")
            page.wait_for_selector("#discoveryBody", state="hidden")
            assert page.is_visible("#syncDiscovery")
            page.screenshot(path=str(SHOTS / "discovery-empty.png"))

            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)

print({"metrics": [3, 85, 4, 2], "percentTotal": 100.0, "distinctiveFirst": "katana",
       "artworkRendered": 0, "responsive": ["1440", "900", "430"], "reset": True,
       "consoleErrors": 0, "screenshots": str(SHOTS)})

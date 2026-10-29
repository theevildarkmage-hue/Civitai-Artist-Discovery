"""The Collectalytics tab in the 3.0 shell, against synthetic API answers.

Only static assets are served; every API answer and still is a fixture, so no app
server, account or external connection is involved. Covers the tab's place and icon,
the owned/followed picker, the stat tiles, the per-day and per-week chart and its
selection, the recent additions with each creator's previous addition, the creator
check and its timeline, the creator bars, and the snippet: its rendered preview, the
HTML put on the clipboard, edits kept per collection, and signing out.
"""
import test_safety  # Fail closed before any test network request.

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, install_fixture

NOW = datetime.now(timezone.utc).replace(microsecond=0)
FOCUS, FOLLOWED = 17980935, 555
COLLECTIONS = [
    {"id": FOCUS, "name": "Music Focus 🎸", "owned": True, "ownerId": 7, "ownerName": None,
     "type": "Image", "read": "Public", "mode": None, "url": f"https://civitai.red/collections/{FOCUS}"},
    {"id": FOLLOWED, "name": "Museum", "owned": False, "ownerId": 9, "ownerName": "Curator",
     "type": "Image", "read": "Public", "mode": None, "url": f"https://civitai.red/collections/{FOLLOWED}"},
]


def item(index, creator, hours_ago, hidden=False):
    item_id = 5000 + index
    return {"collectionItemId": 9000 + index, "kind": "image", "mediaType": "video", "itemId": item_id,
            "addedAt": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
            "createdAt": None, "creatorId": 100 + creator, "creator": f"Composer{creator}",
            "profileUrl": f"https://civitai.red/user/Composer{creator}", "name": f"track{index}.mp4",
            "nsfwLevel": 1, "url": f"https://civitai.red/images/{item_id}",
            # Two videos have no length on Civitai; the rest run 61.5 seconds.
            "duration": None if index in (3, 4) else 61.5,
            "thumbUrl": None if hidden else f"https://image.civitai.com/ca/{item_id}.jpeg",
            **({"thumbHidden": True} if hidden else {})}


# 30 items over about three weeks by 12 creators; Composer0 is added most and most recently.
ITEMS = sorted([item(index, index % 12 if index % 3 else 0, 2 + index * 17, hidden=index == 7)
                for index in range(30)], key=lambda row: row["addedAt"], reverse=True)
CREATORS = len({row["creator"] for row in ITEMS})
REPORTS = {
    FOCUS: {"collection": {**COLLECTIONS[0], "ownerName": "Owner", "description": None},
            "followers": 30, "contributors": 31, "collaborators": 0, "items": ITEMS, "pages": 1,
            "truncated": False, "fetchedAt": NOW.isoformat()},
    FOLLOWED: {"collection": {**COLLECTIONS[1], "description": None}, "followers": None,
               "contributors": None, "collaborators": 0, "items": ITEMS[:3], "pages": 1,
               "truncated": False, "fetchedAt": NOW.isoformat()},
}

server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT / "static")))
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{server.server_port}"
requests = {"collections": [], "collection": [], "image": []}


def routes(route):
    request = route.request
    parsed = urlparse(request.url)
    if parsed.hostname == "image.civitai.com" and parsed.path.startswith("/ca/"):
        route.fulfill(content_type="image/svg+xml",
                      body='<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
                           '<rect width="96" height="96" fill="#365e4d"/></svg>')
        return
    query = parse_qs(parsed.query)
    if parsed.path == "/api/collectalytics/collections":
        requests["collections"].append(query)
        data = {"collections": COLLECTIONS}
    elif parsed.path == "/api/collectalytics/collection":
        requests["collection"].append(query)
        data = REPORTS[int(query["id"][0])]
    elif parsed.path == "/api/collectalytics/image":
        requests["image"].append(query)
        image_id = int(query["id"][0])
        data = {"id": image_id, "type": "video", "username": "Composer0", "url": f"https://image.civitai.com/ca/{image_id}.jpeg",
                "thumbnailUrl": f"https://image.civitai.com/ca/{image_id}.jpeg", "detailImageUrl": f"https://image.civitai.com/ca/{image_id}.jpeg",
                "civitaiUrl": f"https://civitai.red/images/{image_id}", "width": 720, "height": 1280, "baseModel": "Wan",
                "createdAt": NOW.isoformat(), "stats": {"reactionCount": 4}, "prompt": "a guitar solo", "known": True, "tags": []}
    elif parsed.path == "/api/oauth/disconnect":
        data = {"disconnected": True}
    else:
        route.fallback()
        return
    route.fulfill(content_type="application/json", body=json.dumps(data))


def texts(page, selector):
    return page.eval_on_selector_all(selector, "nodes => nodes.map(node => node.textContent)")


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Europe/Amsterdam",
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        install_fixture(page, base_url)
        page.route("**/*", routes)
        page.goto(base_url, wait_until="networkidle")
        page.locator(".creator-card").first.wait_for()

        # The tab sits after Time Machine and before the profile, with its icon.
        order = page.eval_on_selector_all(".view-tabs > button", "buttons => buttons.map(button => button.id)")
        assert order.index("tabTimeMachine") + 1 == order.index("tabCollectalytics") == order.index("tabDiscovery") - 1, order
        assert page.locator("#tabCollectalytics svg.ui-icon").count() == 1
        # Nothing is read until the tab is opened.
        assert requests["collections"] == [], requests

        page.locator("#tabCollectalytics").click()
        page.wait_for_function("() => document.getElementById('tabCollectalytics').getAttribute('aria-pressed') === 'true'")
        assert page.locator("#collectalytics").is_visible() and page.locator("#gallery").is_hidden()
        assert page.locator(".segment-toolbar").is_hidden(), "gallery-only controls leaked onto Collectalytics"
        page.locator("#caBody:not(.hidden)").wait_for()
        assert [query["id"] for query in requests["collection"]] == [[str(FOCUS)]], requests

        # Owned and followed collections are grouped; someone else's names its owner.
        groups = page.eval_on_selector_all("#caCollection optgroup", "groups => groups.map(group => [group.label, [...group.children].map(option => option.textContent)])")
        assert groups == [["Owned", ["Music Focus 🎸"]], ["Followed", ["Museum — Curator"]]], groups

        tiles = texts(page, "#caStats .ca-stat strong")
        labels = texts(page, "#caStats .ca-stat span")
        assert tiles[:3] == ["30", str(CREATORS), "30"], tiles
        assert labels[:3] == ["videos collected", "unique creators", "followers"], labels
        # 28 timed videos of 61.5s: 28m 42s, with the two untimed ones named.
        assert tiles[3] == "28m 42s", tiles
        assert labels[3] == "of video content · 2 videos have no length on Civitai", labels
        assert labels[5].startswith("last added"), labels
        # The intro sits on one line on a wide screen.
        assert page.evaluate("() => { const note = document.querySelector('.ca-head .discovery-note'); return note.getClientRects().length && note.getBoundingClientRect().height < 24; }")
        assert page.locator("#caOpen").get_attribute("href") == COLLECTIONS[0]["url"]
        # The creator check opens on whoever was added last.
        assert page.locator("#caCreator").input_value() == ITEMS[0]["creator"]
        assert page.locator("#caCreatorReport .ca-dot").count() >= 1

        # One bar per day with additions; empty days are kept as gaps, not dropped.
        days = page.evaluate("() => document.querySelectorAll('#caDays .ca-hit').length")
        bars = page.evaluate("() => document.querySelectorAll('#caDays .ca-bar').length")
        added_days = {datetime.fromisoformat(row["addedAt"].replace("Z", "+00:00")).astimezone(
            timezone(timedelta(hours=1))).date() for row in ITEMS}
        assert bars <= len(added_days) and days >= bars and days >= 20, (days, bars)
        page.locator("#caDays .ca-hit").last.hover()
        assert page.locator("#caTooltip").is_visible() and "item" in page.locator("#caTooltip").text_content()
        page.locator("#caDays .ca-hit").last.click()
        page.locator("#caDayItems:not(.hidden)").wait_for()
        assert page.locator("#caDayItems .ca-thumb").count() >= 1
        page.locator("#caBucket button[data-bucket=week]").click()
        page.wait_for_function("() => document.querySelectorAll('#caDays .ca-hit').length <= 5")
        assert page.locator("#caDayItems").is_hidden()

        # The newest addition is first, with how long before it the same creator went in.
        recent = page.locator("#caRecent .ca-recent-row")
        assert recent.count() == 15
        assert recent.first.locator(".ca-creator-link").text_content() == ITEMS[0]["creator"]
        notes = texts(page, "#caRecent .ca-recent-note")
        assert any(note.startswith("Previous from them") for note in notes), notes
        # Every note stays in its row; a shared class name once pulled some out of the list.
        assert page.evaluate("""() => {
            const list = document.getElementById('caRecent').getBoundingClientRect();
            return [...document.querySelectorAll('#caRecent .ca-recent-note')].every(note => {
                const box = note.getBoundingClientRect();
                return box.left >= list.left && box.right <= list.right + 1 && box.top >= list.top && box.bottom <= list.bottom + 1;
            });
        }"""), "a recent-addition note escaped its row"
        assert page.locator("#caRecent .ca-recent-note.soon").count() >= 1
        # A hidden still is a placeholder, not an image.
        assert page.locator(".ca-thumb.missing .ca-thumb-note", has_text="Hidden by your filters").count() >= 1

        # The creator check: the most collected creator, their additions and the timeline.
        page.locator("#caCreator").fill("composer0")
        page.locator("#caCreatorReport .ca-creator-head").wait_for()
        mine = [row for row in ITEMS if row["creator"] == "Composer0"]
        head = page.locator("#caCreatorReport .ca-creator-head").text_content()
        assert head.startswith("Composer0") and f"{len(mine)} items" in head and "#1 of" in head, head
        assert page.locator("#caCreatorReport .ca-verdict").text_content().startswith("Last added")
        assert page.locator("#caCreatorReport .ca-dot").count() == len(mine)
        assert page.locator("#caCreatorReport .ca-tick-mark").count() == len(ITEMS)
        assert page.locator("#caCreatorReport .ca-thumb").count() == len(mine)
        assert page.locator("#caCreatorBars .ca-bar-row.selected .ca-bar-name").text_content() == "Composer0"
        page.locator("#caCreator").fill("Nobody here")
        assert "Nothing from" in page.locator("#caCreatorReport").text_content()

        # A still opens in the app's own detail dialog, read through this tab's route.
        page.locator("#caCreator").fill("Composer0")
        page.locator("#caCreatorReport .ca-thumb").first.click()
        page.wait_for_function("() => document.getElementById('details').open && document.getElementById('detailPrompt').textContent === 'a guitar solo'")
        assert requests["image"][-1]["id"] == [str(mine[0]["itemId"])], requests["image"]
        assert page.locator("#detailCreator").text_content() == "@Composer0"
        assert len(context.pages) == 1, "the click must not open Civitai"
        page.locator("#close").click()
        page.wait_for_function("() => !document.getElementById('details').open")

        # Wide windows: the recent list down the left third; the creator check and the
        # creator bars stacked in the other two thirds; the snippet across the bottom.
        boxes = page.evaluate("""() => Object.fromEntries(['.ca-recent-panel', '.ca-creator-panel', '.ca-bars-panel', '.ca-snippet-panel', '.ca-days-panel']
            .map(selector => { const box = document.querySelector(selector).getBoundingClientRect(); return [selector, [Math.round(box.top), Math.round(box.left), Math.round(box.width)]]; }))""")
        recent, creator, bars, snippet, days = (boxes[key] for key in ('.ca-recent-panel', '.ca-creator-panel', '.ca-bars-panel', '.ca-snippet-panel', '.ca-days-panel'))
        assert recent[0] == creator[0] and creator[2] > 1.8 * recent[2], boxes
        assert bars[1] == creator[1] and bars[0] > creator[0], boxes
        gap = page.evaluate("() => document.querySelector('.ca-bars-panel').getBoundingClientRect().top - document.querySelector('.ca-creator-panel').getBoundingClientRect().bottom")
        assert 0 < gap <= 20, f"the creator bars should follow the creator check directly, not {gap}px below"
        assert snippet[2] == days[2], boxes

        # Bars, most collected first; choosing one opens it in the creator check.
        names = texts(page, "#caCreatorBars .ca-bar-name")
        assert names[0] == "Composer0" and len(names) == CREATORS, names
        page.locator("#caCreatorBars .ca-bar-row").nth(1).click()
        assert page.locator("#caCreator").input_value() == names[1]

        # The snippet: the user's own example, rendered and as markup.
        # The name is the brand: linked exactly as written, with "collection" after it.
        expected = (f'<h2>The <a href="https://civitai.red/collections/{FOCUS}">Music Focus 🎸</a> collection</h2>'
                    f'<p><strong>{CREATORS}</strong> unique creators 🎶<br><strong>30</strong> videos collected<br>'
                    '<strong>30</strong> followers</p>')
        assert page.locator("#caSource").text_content() == expected, page.locator("#caSource").text_content()
        assert page.locator("#caPreview h2 a").get_attribute("href") == f"https://civitai.red/collections/{FOCUS}"
        assert texts(page, "#caPreview strong") == [str(CREATORS), "30", "30"]
        page.locator("#caCopyRich").click()
        page.wait_for_function("() => document.getElementById('caCopyStatus').textContent.startsWith('Copied')")
        clipboard = page.evaluate("""async () => {
            const [entry] = await navigator.clipboard.read();
            return { html: await (await entry.getType('text/html')).text(),
                     text: await (await entry.getType('text/plain')).text() };
        }""")
        assert expected in clipboard["html"], clipboard
        assert clipboard["text"] == f"The Music Focus 🎸 collection\n{CREATORS} unique creators 🎶\n30 videos collected\n30 followers", clipboard
        page.locator("#caCopySource").click()
        page.wait_for_function("() => document.getElementById('caCopyStatus').textContent === 'HTML copied.'")
        assert page.evaluate("() => navigator.clipboard.readText()") == expected

        # Typed text is escaped, never markup; edits are kept for this collection only.
        page.locator("#caTitle").fill("<b>Our</b> [picks & <i>jams</i>] {items}!")
        page.locator("#caHeading").select_option("p")
        source = page.locator("#caSource").text_content()
        assert source.startswith('<p><strong>&lt;b&gt;Our&lt;/b&gt; <a href="https://civitai.red/collections/17980935">picks &amp; &lt;i&gt;jams&lt;/i&gt;</a> 30!</strong></p>'), source
        assert page.locator("#caPreview b, #caPreview i").count() == 0
        # {duration} becomes the summed video length, bold.
        page.locator("#caLines").fill("{duration} of video content")
        assert page.locator("#caSource").text_content().endswith("<p><strong>28m 42s</strong> of video content</p>"), page.locator("#caSource").text_content()
        # The same goes for the lines: only the placeholders become markup.
        page.locator("#caLines").fill('<em id="injected">Literal</em> {items} & more')
        assert page.locator("#caPreview em, #injected").count() == 0
        assert '<br>' not in page.locator("#caSource").text_content().split('</p><p>')[-1]
        assert page.locator("#caSource").text_content().endswith(
            '<p>&lt;em id=&quot;injected&quot;&gt;Literal&lt;/em&gt; <strong>30</strong> &amp; more</p>'), page.locator("#caSource").text_content()
        # While another collection loads, the editor is locked so nothing is saved under it.
        locked = page.evaluate("""() => {
            const select = document.getElementById('caCollection');
            select.value = '%d';
            select.dispatchEvent(new Event('change'));
            return ['caTitle', 'caLines', 'caHeading', 'caCopyRich'].every(id => document.getElementById(id).disabled);
        }""" % FOLLOWED)
        assert locked, "the snippet editor stayed editable during a collection change"

        # A followed collection without a follower count says so in the snippet.
        page.wait_for_function("() => document.querySelectorAll('#caStats .ca-stat').length && document.getElementById('caOpen').textContent.includes('Museum')")
        assert page.locator("#caOwner").text_content() == "By Curator"
        assert texts(page, "#caStats .ca-stat strong")[2] == "—"
        assert page.locator("#caFollowersNote").is_visible()
        assert "<strong>?</strong> followers" in page.locator("#caSource").text_content()
        assert page.locator("#caTitle").input_value() == "The [Museum] collection"

        assert not page.locator("#caTitle").is_disabled()
        # Music Focus's edits were not saved under Museum.
        assert "injected" not in page.evaluate("() => JSON.stringify(JSON.parse(localStorage.getItem('civitaiCollectalyticsSnippet'))['%d'] || {})" % FOLLOWED)

        # Filters changed while the tab is hidden: the report is reread on return.
        before = len(requests["collection"])
        page.locator("#tabGallery").click()
        page.evaluate("() => window.dispatchEvent(new Event('community-controls-changed'))")
        assert len(requests["collection"]) == before, "a hidden tab must not read"
        page.locator("#tabCollectalytics").click()
        page.wait_for_function("count => document.querySelectorAll('#caBody:not(.hidden)').length", arg=1)
        assert len(requests["collection"]) == before + 1, requests["collection"]

        # A reload restores the tab, the collection and the edited snippet.
        page.locator("#caCollection").select_option(str(FOCUS))
        page.wait_for_function("() => document.getElementById('caOpen').textContent.includes('Music Focus')")
        page.locator("#caCreator").fill("Composer0")
        page.screenshot(path=str(test_safety.REPORTS / "collectalytics-desktop.png"), full_page=True)
        page.reload(wait_until="networkidle")
        page.wait_for_function("() => document.getElementById('tabCollectalytics').getAttribute('aria-pressed') === 'true'")
        page.wait_for_function("() => document.getElementById('caOpen').textContent.includes('Music Focus')")
        assert page.locator("#caHeading").input_value() == "p"
        # Only edited fields are kept, so the default title still follows the name.
        page.locator("#caSnippetReset").click()
        page.locator("#caHeading").select_option("h3")
        kept = page.evaluate("() => JSON.parse(localStorage.getItem('civitaiCollectalyticsSnippet'))['%d']" % FOCUS)
        assert kept == {"heading": "h3"}, kept
        page.locator("#caSnippetReset").click()
        assert page.locator("#caSource").text_content() == expected

        # All five tabs still share one row in narrow desktop windows.
        for width in (701, 800, 899, 900, 1000, 1099, 1100):
            page.set_viewport_size({"width": width, "height": 800})
            page.wait_for_timeout(100)
            tops = page.eval_on_selector_all(".view-tabs > button", "buttons => buttons.map(button => Math.round(button.getBoundingClientRect().top))")
            assert len(set(tops[:6])) == 1, (width, tops)
        page.set_viewport_size({"width": 420, "height": 860})
        page.wait_for_timeout(200)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, overflow
        # The chart is redrawn for the narrow frame rather than kept at desktop width.
        page.wait_for_function("() => { const svg = document.getElementById('caDays'); return Number(svg.getAttribute('width')) <= svg.parentElement.clientWidth + 1 || document.querySelectorAll('#caDays .ca-hit').length > 12; }")
        page.screenshot(path=str(test_safety.REPORTS / "collectalytics-mobile.png"), full_page=True)

        # The last opened collection is remembered in a new window, not just this one.
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#caCollection").select_option(str(FOLLOWED))
        page.wait_for_function("() => document.getElementById('caOpen').textContent.includes('Museum')")
        second = context.new_page()
        install_fixture(second, base_url)
        second.route("**/*", routes)
        before = len(requests["collection"])
        second.goto(base_url, wait_until="networkidle")
        second.locator("#tabCollectalytics").click()
        second.wait_for_function("() => document.getElementById('caOpen').textContent.includes('Museum')")
        assert requests["collection"][before]["id"] == [str(FOLLOWED)], requests["collection"][before:]
        assert second.locator("#caCollection").input_value() == str(FOLLOWED)
        second.close()

        # Signing out leaves no collection data on screen.
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("#disconnect").click()
        page.locator("#welcome:not(.hidden)").wait_for()
        assert page.locator("#collectalytics").is_hidden()
        assert errors == [], errors
        browser.close()
finally:
    server.shutdown()
print("collectalytics_browser passed")

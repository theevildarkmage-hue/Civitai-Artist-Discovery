"""Repeatable, offline three-page screenshots and browser timings for the v2 refresh.

Run: py tests/v2_visual_baseline.py --label baseline
Only static assets are served; API and artwork responses are synthetic. No app server,
account, archive, credentials, or external connection is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DAY = (datetime.now() - timedelta(days=1)).date().isoformat()


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def creator(index):
    name = f"Studio_{index:03d}"
    url = f"https://image.civitai.com/fixture/{index}.svg"
    return {"username": name, "userId": index, "imageCount": 1,
            "profileUrl": f"https://civitai.red/user/{name}", "following": index % 3 == 0,
            "followers": 120 + index, "seen": False, "seenCount": index,
            "knownCount": 80 + index, "complete": True,
            "representative": {"id": index, "createdAt": f"{DAY}T12:00:00Z",
                "url": url, "thumbnailUrl": url, "width": 768, "height": 1024,
                "browsingLevel": 1, "baseModel": "SDXL",
                "civitaiUrl": f"https://civitai.red/images/{index}",
                "stats": {"likeCount": 12 + index, "heartCount": 8,
                          "laughCount": 1, "cryCount": 0, "reactionCount": 21 + index}}}


def install_fixture(page, base_url, *, cached_tags=False):
    cards = [creator(i) for i in range(1, 121)]
    if cached_tags:
        for card in cards:
            card['representative']['tagState'] = {'known': True, 'tags': []}
    status = {"complete": True, "archiveComplete": True, "state": "complete",
              "itemCount": 120, "creatorCount": 120, "contentRating": "Soft"}
    settings = {"browsingLevels": [1, 2], "contentRating": "Soft", "checkForUpdates": False}
    summary = {"hasData": True, "lastSyncAt": datetime.now(timezone.utc).isoformat(),
               "followedCreators": 42, "reactedImages": 1200, "creatorsReactedTo": 86,
               "creatorsNotFollowed": 3, "reactionRecords": 1200, "baselineImages": 500,
               "reactionMix": [{"reaction": "Heart", "count": 800, "percent": 66.7},
                               {"reaction": "Like", "count": 400, "percent": 33.3}],
               "topTags": [{"name": "landscape", "images": 480, "percent": 40}],
               "distinctiveTags": [{"name": "watercolor", "images": 200, "lift": 3.2}],
               "topCreators": [{"username": "Studio_001", "images": 30, "following": True}],
               "reactedNotFollowed": [{"id": 2, "username": "Studio_002", "images": 18}]}

    def route_request(route):
        parsed = urlparse(route.request.url)
        if parsed.hostname == "image.civitai.com":
            number = int(Path(parsed.path).stem)
            color = ["#275a70", "#70512c", "#56446e", "#365e4d"][number % 4]
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="768" height="1024">'
                   f'<rect width="768" height="1024" fill="{color}"/>'
                   '<circle cx="550" cy="220" r="130" fill="#fff" opacity=".15"/>'
                   '<path d="M0 900L300 370L768 1024H0Z" fill="#101820" opacity=".5"/>'
                   f'<text x="45" y="100" fill="white" font-size="32">Study {number:03}</text></svg>')
            route.fulfill(content_type="image/svg+xml", body=svg)
            return
        if not route.request.url.startswith(base_url):
            route.abort()
            return
        if not parsed.path.startswith("/api/"):
            route.continue_()
            return
        query = parse_qs(parsed.query)
        path = parsed.path
        data = {}
        if path == "/api/auth-status":
            data = {"connected": True, "socialWrite": True, "username": "Preview", "id": 7}
        elif path == "/api/settings":
            if route.request.method == 'POST':
                settings.update(json.loads(route.request.post_data))
            data = settings
        elif path == "/api/discovery/summary":
            data = summary
        elif path == "/api/history/config":
            data = {"version": "1.0.4", "hasArchives": True}
        elif path == "/api/history/status":
            data = status
        elif path == "/api/history/blocks":
            data = {"blocks": {key: status for key in ("all", "morning", "evening")}}
        elif path == "/api/history/day":
            data = {**status, "artistCount": 120, "imageCount": 120}
        elif path == "/api/history/calendar":
            data = {"days": [{"date": DAY, "all": True, "morning": True,
                               "evening": True}]}
        elif path == "/api/history/artists":
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [50])[0])
            data = {"artists": cards[offset:offset + limit], "total": 120,
                    "hasMore": offset + limit < 120}
        elif path == "/api/history/tags":
            ids = json.loads(route.request.post_data)["imageIds"]
            data = {"images": {str(i): {"known": True, "tags": []} for i in ids}}
        elif path == "/api/history/models":
            data = {"models": [{"model": "SDXL", "images": 120}, {"model": "Flux", "images": 60}]}
        elif path.startswith("/api/history/prepare"):
            data = {"complete": True, "known": 120, "total": 120, "job": {"running": False}}
        elif path == "/api/timemachine":
            data = {"cards": cards[:12], "status": {"creators": 12, "primed": 12,
                    "images": 1024, "priming": False, "progress": 100}}
        elif path == "/api/creator-metadata":
            data = {"creators": {}}
        elif path == "/api/reaction-status":
            data = {"images": {}}
        elif path == "/api/update/status":
            data = {"currentVersion": "1.0.4", "supported": False}
        route.fulfill(content_type="application/json", body=json.dumps(data))

    page.route("**/*", route_request)


def capture(label):
    if not label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Use an alphanumeric label with optional hyphens/underscores")
    output = ROOT / "reports" / "v2" / label
    output.mkdir(parents=True, exist_ok=True)
    handler = partial(StaticHandler, directory=str(ROOT / "static"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, height in [(390, 844), (1366, 768), (1920, 1080)]:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                install_fixture(page, base_url)
                page.goto(base_url, wait_until="networkidle")
                page.wait_for_selector(".creator-card .image-button img[src]")
                for tab, section in [("tabGallery", "gallery"), ("tabTimeMachine", "timeMachine"),
                                     ("tabDiscovery", "discovery")]:
                    page.locator(f"#{tab}").click()
                    page.locator(f"#{section}").wait_for()
                    page.wait_for_timeout(200)
                    page.screenshot(path=str(output / f"{section}-{width}.png"))
                    if section == 'gallery' and page.locator('#filterToggle').count():
                        page.locator('#calendarToggle').click()
                        page.locator('#calendarPanel [aria-current="date"]').wait_for()
                        page.screenshot(path=str(output / f"calendar-{width}.png"))
                        page.get_by_role('button', name='Close calendar', exact=True).click()
                        page.locator('#filterToggle').click()
                        page.locator('#modelMenu input').first.wait_for()
                        page.screenshot(path=str(output / f"filters-{width}.png"))
                        page.get_by_role('button', name='Close filters', exact=True).click()
                        page.locator('#galleryPreferences').click()
                        page.locator('#cardSizeSlider').wait_for()
                        page.screenshot(path=str(output / f"preferences-{width}.png"))
                        page.get_by_role('button', name='Close gallery preferences', exact=True).click()
                    results.append({"page": section, "width": width,
                        "horizontalOverflow": page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"),
                        "resources": page.evaluate(r"""() => performance.getEntriesByType('resource')
                            .filter(r => /api\/|fixture\//.test(r.name))
                            .map(r => ({path: new URL(r.name).pathname,
                                startMs: Math.round(r.startTime), durationMs: Math.round(r.duration)}))""")})
                assert not errors, errors
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (output / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "views": len(results),
                      "overflow": [{"page": r["page"], "width": r["width"]}
                                   for r in results if r["horizontalOverflow"]]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="current")
    capture(parser.parse_args().label)

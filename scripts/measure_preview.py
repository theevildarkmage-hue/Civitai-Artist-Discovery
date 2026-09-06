"""Read-only browser timing of an already-built local day; prints aggregate metrics only.

No follows, reactions, settings changes, seen writes, builds, or analysis are allowed.
Tag-verification reads use their existing POST endpoint. No screenshots are saved.
"""
import argparse
import json
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url')
parser.add_argument('--date', required=True)
parser.add_argument('--segment', choices=['all', 'morning', 'evening'], default='all')
args = parser.parse_args()
assert urlparse(args.url).hostname in {'127.0.0.1', 'localhost', '::1'}, 'Use the local app URL'
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1366, 'height': 768})
    blocked = []

    def read_only(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method not in {'GET', 'HEAD'} and path != '/api/history/tags':
            blocked.append(path)
            route.fulfill(status=409, content_type='application/json',
                          body='{"error":"Read-only performance measurement"}')
        else:
            route.continue_()

    page.route('**/*', read_only)
    snapshot = {'date': args.date, 'segment': args.segment, 'view': 'discovery',
                'models': [], 'loaded': 0, 'scrollY': 0}
    page.add_init_script('sessionStorage.setItem("civitai-feed-state", ' +
                         json.dumps(json.dumps(snapshot)) + ');')
    results = []
    for label in ['fresh-browser', 'warm-refresh']:
        page.goto(args.url.rstrip('/') + '/?uiPerf=1', wait_until='domcontentloaded')
        deadline = time.monotonic() + 20
        while True:
            metrics = page.evaluate('() => window.CivitaiPerformance?.snapshot() || {}')
            if metrics.get('firstPreviewFrameMs') is not None or time.monotonic() >= deadline:
                break
            page.wait_for_timeout(200)
        outcome = 'preview-loaded' if metrics.get('firstPreviewFrameMs') is not None else 'no-preview-within-20s'
        results.append({'scenario': label, 'outcome': outcome, **metrics})
    browser.close()
    print(json.dumps({'results': results, 'blockedWriteEndpoints': sorted(set(blocked))}))

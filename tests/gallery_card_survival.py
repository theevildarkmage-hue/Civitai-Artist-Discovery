"""Slow tag checks and unavailable images do not silently remove unfiltered cards."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading
import time

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, install_fixture


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f'http://127.0.0.1:{server.server_port}'

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scenario in ('slow-tags', 'failed-tags', 'failed-images', 'hidden-tag'):
            page = browser.new_page(viewport={'width': 1366, 'height': 900})
            install_fixture(page, base_url)
            removals, errors = [], []
            page.on('console', lambda message: removals.append(message.text)
                    if message.text.startswith('Gallery card removed') else None)
            page.on('pageerror', lambda error: errors.append(str(error)))

            def tags(route):
                if scenario == 'failed-tags':
                    route.fulfill(status=503, content_type='application/json', body='{"error":"Unavailable"}')
                    return
                if scenario == 'slow-tags':
                    time.sleep(.15)
                ids = json.loads(route.request.post_data)['imageIds']
                values = {str(image_id): {'known': True, 'tags': [
                    {'name': 'account-hidden-topic', 'hidden': True}
                ] if scenario == 'hidden-tag' and image_id == 1 else []} for image_id in ids}
                route.fulfill(content_type='application/json', body=json.dumps({'images': values}))

            page.route('**/api/history/tags', tags)
            if scenario == 'failed-images':
                page.route('https://image.civitai.com/**', lambda route: route.fulfill(status=503, body='Unavailable'))
            page.goto(base_url, wait_until='networkidle')
            page.locator('.creator-card').first.wait_for()
            page.wait_for_timeout(3300 if scenario == 'failed-tags' else 500)
            if scenario == 'hidden-tag':
                assert page.locator('.creator-card[data-id="1"]').count() == 0
                assert removals and all('all-images-hidden-by-civitai-tags' in message for message in removals), removals
            else:
                assert not removals, (scenario, removals)
                count = page.locator('.creator-card').count()
                assert count >= 24, (scenario, count)
                if scenario == 'failed-tags':
                    assert page.locator('.tag-check-failed').count() > 0
                elif scenario == 'failed-images':
                    assert page.locator('.creator-card .image-error').count() > 0
                else:
                    assert page.locator('.creator-card .image-button img[src]').count() > 0
                page.wait_for_timeout(300)
                assert page.locator('.creator-card').count() >= count
            assert not errors, (scenario, errors)
            page.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()

print({'slowTagsPreserveCards': True, 'failedTagsPreserveCards': True,
       'failedImagesPreserveCards': True, 'hiddenTagRemovalExplained': True})

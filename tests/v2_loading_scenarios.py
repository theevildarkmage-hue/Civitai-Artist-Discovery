"""Offline loading measurements with explicit cold-tag latency and warm cache.

Reports browser timings, not live Civitai/CDN speed. Assertions cover bounded paging,
content verification, filtered reloads and deep-scroll restoration.
"""
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, install_fixture

TRACE = """() => {
    window.loadingTrace = {start: performance.now(), skeletonMs: null, imageMs: null};
    window.loadingIgnoredImages = new WeakSet();
    new MutationObserver(() => {
        const trace = window.loadingTrace;
        if (trace.skeletonMs === null && document.querySelector('.gallery-skeleton'))
            trace.skeletonMs = Math.round(performance.now() - trace.start);
    }).observe(document, {subtree: true, childList: true});
    document.addEventListener('load', event => {
        const trace = window.loadingTrace;
        if (event.target.matches?.('.image-button img') && trace.imageMs === null &&
            !window.loadingIgnoredImages.has(event.target))
            trace.imageMs = Math.round(performance.now() - trace.start);
    }, true);
}"""


def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for cached in (False, True):
                page = browser.new_page(viewport={'width': 1366, 'height': 768})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                install_fixture(page, base_url, cached_tags=cached)
                page.add_init_script(f'({TRACE})()')
                held = []
                tag_calls = []

                def hold_tags(route):
                    ids = json.loads(route.request.post_data)['imageIds']
                    tag_calls.append(ids)
                    held.append((route, ids))

                page.route('**/api/history/tags', hold_tags)
                page.goto(base_url, wait_until='domcontentloaded')
                page.locator('.creator-card').first.wait_for()
                assert page.locator('.creator-card').count() == 24
                if not cached:
                    # Inject a known 250ms verification delay without blocking the
                    # browser's rendering/event loop; previews must stay unassigned.
                    page.wait_for_timeout(250)
                    assert held
                    assert page.locator('.image-button img[src]').count() == 0
                    for route, ids in held:
                        route.fulfill(content_type='application/json', body=json.dumps(
                            {'images': {str(i): {'known': True, 'tags': []} for i in ids}}))
                page.wait_for_function('window.loadingTrace.imageMs !== null')
                trace = page.evaluate('window.loadingTrace')
                trace.pop('start')
                results.append({'scenario': 'warm-refresh' if cached else 'cold-tags-250ms',
                                **trace, 'tagBatches': len(tag_calls),
                                'initialCards': page.locator('.creator-card').count()})
                if cached:
                    assert not tag_calls
                    for view in ('discovery', 'new', 'foryou'):
                        page.evaluate("window.loadingIgnoredImages = new WeakSet(document.querySelectorAll('.image-button img')); window.loadingTrace = {start: performance.now(), skeletonMs: null, imageMs: null}")
                        page.locator('#dayView').select_option(view)
                        page.wait_for_function('window.loadingTrace.imageMs !== null')
                        trace = page.evaluate('window.loadingTrace')
                        trace.pop('start')
                        results.append({'scenario': f'feed-{view}', **trace})
                    page.locator('#modelFilter').click()
                    page.evaluate("window.loadingIgnoredImages = new WeakSet(document.querySelectorAll('.image-button img')); window.loadingTrace = {start: performance.now(), skeletonMs: null, imageMs: null}")
                    page.locator('#modelMenu input').first.check()
                    page.wait_for_function('window.loadingTrace.imageMs !== null')
                    trace = page.evaluate('window.loadingTrace')
                    trace.pop('start')
                    results.append({'scenario': 'model-SDXL', **trace})
                    assert page.evaluate("JSON.parse(sessionStorage.getItem('civitai-feed-state')).models") == ['SDXL']
                    page.locator('#modelFilter').click()
                    page.locator('#contentFilter').click()
                    page.evaluate("window.loadingIgnoredImages = new WeakSet(document.querySelectorAll('.image-button img')); window.loadingTrace = {start: performance.now(), skeletonMs: null, imageMs: null}")
                    page.locator('#contentMenu [data-level="2"]').click()
                    page.wait_for_function('window.loadingTrace.imageMs !== null')
                    assert page.locator('#contentFilter').inner_text() == 'Content: PG'
                    trace = page.evaluate('window.loadingTrace')
                    trace.pop('start')
                    results.append({'scenario': 'content-PG', **trace})
                    page.mouse.wheel(0, 100000)
                    page.locator('.creator-card').nth(50).wait_for()
                    loaded = page.locator('.creator-card').count()
                    page.reload(wait_until='networkidle')
                    try:
                        page.wait_for_function('(count) => document.querySelectorAll(".creator-card").length >= count', arg=loaded, timeout=5000)
                    except Exception as error:
                        raise AssertionError({'expected': loaded, 'actual': page.locator('.creator-card').count(),
                            'saved': page.evaluate("sessionStorage.getItem('civitai-feed-state')"), 'errors': errors}) from error
                    assert page.locator('#dayView').input_value() == 'foryou'
                    assert page.locator('#modelFilter').inner_text() == 'Model: SDXL'
                    assert page.locator('#contentFilter').inner_text() == 'Content: PG'
                    assert page.evaluate('scrollY') > 0
                    results.append({'scenario': 'filtered-scroll-restore', 'loadedBefore': loaded,
                                    'loadedAfter': page.locator('.creator-card').count()})
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    output = ROOT / 'reports' / 'v2' / 'loading-scenarios.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    run()

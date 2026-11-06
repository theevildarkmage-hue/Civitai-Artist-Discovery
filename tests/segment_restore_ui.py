"""An explicitly saved, complete half-day survives refresh even when All day exists."""
import test_safety  # Refuse live application endpoints and isolate all test data.
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, DAY, StaticHandler, install_fixture

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for segment in ('morning', 'evening'):
            page = browser.new_page()
            base = f'http://127.0.0.1:{server.server_port}'
            install_fixture(page, base, cached_tags=True)
            saved = {'date': DAY, 'segment': segment, 'view': 'discovery',
                     'models': [], 'loaded': 0, 'scrollY': 0}
            page.add_init_script('sessionStorage.setItem("civitai-feed-state", ' + json.dumps(json.dumps(saved)) + ')')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for action in (lambda: page.goto(base), lambda: page.reload()):
                action()
                page.locator('.creator-card').first.wait_for()
                assert page.locator('#daySegment').input_value() == segment
                assert segment.capitalize() in page.locator('#calendarToggle').inner_text()
                assert not errors, errors
            page.close()
        browser.close()
    print({'morningRefresh': True, 'eveningRefresh': True, 'allDayDoesNotOverrideExplicitWindow': True})
finally:
    server.shutdown()
    server.server_close()

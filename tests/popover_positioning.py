"""Toolbar popovers stay beside their actual opener and within the viewport."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from functools import partial
from http.server import ThreadingHTTPServer
import threading

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, install_fixture


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'


def check_position(page, trigger, panel):
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    geometry = page.evaluate('''([trigger, panel]) => {
        const a=document.querySelector(trigger).getBoundingClientRect();
        const p=document.querySelector(panel).getBoundingClientRect();
        return {anchor:{left:a.left,top:a.top,bottom:a.bottom},
            panel:{left:p.left,right:p.right,top:p.top,bottom:p.bottom,width:p.width},
            width:innerWidth,height:innerHeight};
    }''', [trigger, panel])
    a, p = geometry['anchor'], geometry['panel']
    expected_left = max(12, min(a['left'], geometry['width'] - p['width'] - 12))
    assert abs(p['left'] - expected_left) < 2, geometry
    assert p['left'] >= 11 and p['right'] <= geometry['width'] - 11, geometry
    assert p['top'] >= 11 and p['bottom'] <= geometry['height'] - 11, geometry
    assert abs(p['top'] - a['bottom'] - 8) < 2 or abs(p['bottom'] - a['top'] + 8) < 2, geometry


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height in ((1920, 1080), (1366, 900), (390, 844), (800, 500)):
            page = browser.new_page(viewport={'width': width, 'height': height})
            install_fixture(page, base)
            page.goto(base, wait_until='networkidle')
            page.locator('.creator-card').first.wait_for()
            for trigger, panel, close in (
                ('#calendarToggle', '#calendarPanel', 'Close calendar'),
                ('#filterToggle', '#filterPanel', 'Close filters'),
                ('#galleryPreferences', '#preferencesMenu', 'Close gallery preferences'),
            ):
                page.locator(trigger).click()
                page.locator(panel).wait_for(state='visible')
                check_position(page, trigger, panel)
                page.evaluate('window.scrollTo(0, 350)')
                check_position(page, trigger, panel)
                page.set_viewport_size({'width': max(360, width - 100), 'height': height})
                check_position(page, trigger, panel)
                page.get_by_role('button', name=close, exact=True).click()
                page.set_viewport_size({'width': width, 'height': height})
                page.evaluate('window.scrollTo(0, 0)')
            page.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()

print({'calendarAnchored': True, 'filtersAndContentAnchored': True,
       'settingsAnchored': True, 'viewportSizes': 4, 'scrollAndResize': True})

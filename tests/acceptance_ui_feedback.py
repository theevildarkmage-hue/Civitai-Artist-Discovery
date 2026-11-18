"""Offline browser regression for dismissible completion and explicit calendar loading."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, DAY, StaticHandler, install_fixture

server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        install_fixture(page, base)
        page.goto(base, wait_until='networkidle')
        page.locator('.creator-card').first.wait_for()
        page.evaluate("showSweepCompletion(document.getElementById('followerSweep'), 'tags', {known:4387, unavailable:7})")
        note = page.locator('#followerSweep')
        assert '4,387' in note.inner_text()
        dismiss = page.get_by_role('button', name='Dismiss tag preparation summary')
        dismiss.focus()
        page.keyboard.press('Enter')
        assert note.is_hidden()
        page.evaluate("showSweepProgress(document.getElementById('followerSweep'), 'tags', {known:1, total:2, unavailable:0})")
        assert note.is_visible(), 'dismissal must not suppress subsequent work'
        page.evaluate('hideSweepNote()')
        pending = []
        page.route('**/api/history/calendar', lambda route: pending.append(route))
        page.locator('#calendarToggle').click()
        page.wait_for_function("document.querySelector('#calendarPanel').getAttribute('aria-busy') === 'true'")
        assert page.locator('.calendar-status').is_visible()
        page.wait_for_timeout(100)
        assert len(pending) == 1
        pending.pop().fulfill(content_type='application/json', body=json.dumps({'days':[{'date':DAY,'morning':True}]}))
        cell = page.locator(f'#calendarPanel [data-date="{DAY}"]')
        page.wait_for_function("date => document.querySelector(`[data-date='${date}']`).classList.contains('partial')", arg=DAY)
        assert page.locator('.calendar-status').is_hidden()
        page.get_by_role('button', name='Close calendar', exact=True).click()
        page.locator('#calendarToggle').click()
        assert 'partial' in cell.get_attribute('class'), 'cached coverage flashed away'
        page.wait_for_timeout(100)
        pending.pop().fulfill(status=503, content_type='application/json', body='{"error":"Offline"}')
        page.wait_for_function("document.querySelector('.calendar-status').textContent.includes('could not refresh')")
        assert 'partial' in cell.get_attribute('class')
        page.get_by_role('button', name='Close calendar', exact=True).click()
        page.get_by_role('button', name='My Profile', exact=True).click()
        page.locator('#summaryRow .metric-card .hint').first.wait_for(state='visible')
        assert len(page.locator('#summaryRow .metric-card .hint').all()) == 4
        assert not errors, errors
        browser.close()
finally:
    server.shutdown()
    server.server_close()
print({'dismissibleSummary':True,'newProgressVisible':True,'calendarLoadingAndOffline':True,'cachedMarkersRetained':True,'profileCaptionsVisible':True})

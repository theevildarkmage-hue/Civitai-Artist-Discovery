"""An overnight tab discovers synced dates without reloading or changing galleries."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import date, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading

from playwright.sync_api import sync_playwright
from v2_visual_baseline import DAY, ROOT, StaticHandler, install_fixture


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base_url = f'http://127.0.0.1:{server.server_port}'
next_day = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1366, 'height': 900}, timezone_id='America/Chicago')
        page.clock.install(time=f'{next_day}T23:59:50-05:00')
        install_fixture(page, base_url)
        phase = {'morning': False, 'all': False, 'down': False, 'requests': 0}
        errors, artist_requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: artist_requests.append(request.url)
                if '/api/history/artists?' in request.url else None)

        def calendar(route):
            phase['requests'] += 1
            if phase['down']:
                route.fulfill(status=503, content_type='application/json', body='{"error":"Unavailable"}')
                return
            days = [{'date': DAY, 'all': True, 'morning': True, 'evening': True}]
            if phase['morning']:
                days.append({'date': next_day, 'all': phase['all'],
                             'morning': True, 'evening': phase['all']})
            route.fulfill(content_type='application/json', body=json.dumps({'days': days}))

        page.route('**/api/history/calendar', calendar)
        page.goto(base_url, wait_until='networkidle')
        page.locator('.creator-card').first.wait_for()
        page.locator('#calendarToggle').click()
        page.locator(f'#calendarPanel [data-date="{DAY}"].saved').wait_for()
        latest = page.locator(f'#calendarPanel [data-date="{next_day}"]')
        assert latest.is_disabled(), 'today was selectable before it ended'
        before = page.evaluate('galleryCalendarState()')
        initial_artist_requests = len(artist_requests)

        # Midnight passes while unattended collection finishes the first half.
        phase['morning'] = True
        requests = phase['requests']
        page.clock.fast_forward(35_000)
        assert phase['requests'] == requests, 'open calendar was polling'
        page.get_by_role('button', name='Close calendar', exact=True).click()
        page.locator('#calendarToggle').click()
        page.wait_for_function('expected => galleryCalendarState().newest === expected', arg=next_day)
        page.locator(f'#calendarPanel [data-date="{next_day}"].partial').wait_for()
        assert latest.is_enabled(), 'the overnight date boundary stayed stale'
        assert page.locator('#newerDay').is_enabled()

        # A local build completion explicitly refreshes an already open panel.
        phase['all'] = True
        page.evaluate('window.updateGalleryCalendar()')
        page.locator(f'#calendarPanel [data-date="{next_day}"].saved').wait_for()
        assert page.evaluate('galleryCalendarState().date') == before['date']
        assert len(artist_requests) == initial_artist_requests, 'calendar refresh reloaded the gallery'

        # Outages preserve the last known hints; returning to the tab refreshes immediately.
        phase['down'] = True
        page.evaluate('window.updateGalleryCalendar()')
        assert 'saved' in latest.get_attribute('class')
        phase['down'] = False
        requests = phase['requests']
        with page.expect_response('**/api/history/calendar'):
            page.evaluate('window.dispatchEvent(new Event("focus"))')
        assert phase['requests'] > requests

        # Browsing another month stays there as updates arrive.
        page.locator('#calendarPanel [data-month="-1"]').click()
        title = page.locator('.calendar-title').inner_text()
        page.clock.fast_forward(30_000)
        assert page.locator('.calendar-title').inner_text() == title
        page.get_by_role('button', name='Close calendar', exact=True).click()
        requests = phase['requests']
        page.clock.fast_forward(60_000)
        assert phase['requests'] == requests, 'closed calendar kept fetching coverage'
        assert not errors, errors
        browser.close()
finally:
    server.shutdown()
    server.server_close()

print({'overnightDateEnabled': True, 'partialThenComplete': True,
       'selectionPreserved': True, 'offlineHintsPreserved': True, 'closedCalendarDoesNotPoll': True})

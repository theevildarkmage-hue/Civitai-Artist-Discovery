"""Shared shell keyboard behavior, module transport, and image fallback contract."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, install_fixture, creator


def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            install_fixture(page, base_url)
            page.goto(base_url, wait_until='networkidle')
            page.locator('.creator-card').first.wait_for()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            state = page.evaluate('''async () => {
                const {browsingState, modelParameters, readBrowsingState} = await import('/ui/state.js');
                const value = browsingState({date:'bad', segment:'bad', view:'bad',
                    levels:[], models:['Flux & XL', 'Flux & XL', null, ''], loaded:Infinity, scrollY:-5});
                return {value, query:modelParameters(value),
                    broken:readBrowsingState({getItem:()=>'{broken'}, 'key'),
                    roundTrip:readBrowsingState({getItem:()=>JSON.stringify(value)}, 'key')};
            }''')
            assert state['value'] == state['roundTrip']
            assert state['value']['levels'] == [1, 2] and state['value']['view'] == 'foryou'
            assert state['value']['loaded'] == 0 and state['value']['scrollY'] == 0
            assert state['broken'] is None and state['query'] == '&model=Flux%20%26%20XL'
            follow = page.evaluate('''async () => {
                const {toggleCreatorFollow} = await import('/ui/creator-actions.js');
                const button = document.createElement('button');
                const creator = {username:'Fixture', userId:1, following:false};
                let calls = 0, allowed = false;
                const actions = {canWrite:()=>allowed, toast:()=>{},
                    api:async()=>{ calls++; allowed=false; return {following:true, userId:1}; }};
                await toggleCreatorFollow(button, creator, actions);
                const blocked = calls === 0;
                allowed = true;
                await toggleCreatorFollow(button, creator, actions);
                return {blocked, calls, following:creator.following, disabled:button.disabled,
                    pending:!!button.dataset.followPending};
            }''')
            assert follow == {'blocked': True, 'calls': 1, 'following': True,
                              'disabled': True, 'pending': False}
            trigger = page.locator('#contentFilter')
            trigger.focus()
            trigger.press('ArrowDown')
            page.wait_for_function("document.querySelector('#contentMenu').contains(document.activeElement)")
            page.keyboard.press('Escape')
            assert page.locator('#contentMenu').is_hidden()
            assert trigger.get_attribute('aria-expanded') == 'false'
            assert trigger.evaluate('node => node === document.activeElement')
            # Enter/Space leave focus on the opener; Escape must still dismiss.
            for trigger_id, panel_id in [('filterToggle', 'filterPanel'),
                                         ('contentFilter', 'filterPanel'),
                                         ('galleryPreferences', 'preferencesMenu')]:
                keyboard_trigger = page.locator(f'#{trigger_id}')
                keyboard_trigger.focus()
                keyboard_trigger.press('Enter')
                assert page.locator(f'#{panel_id}').is_visible()
                keyboard_trigger.press('Escape')
                assert page.locator(f'#{panel_id}').is_hidden()
                assert keyboard_trigger.get_attribute('aria-expanded') == 'false'
                assert keyboard_trigger.evaluate('node => node === document.activeElement')
            page.locator('#tabTimeMachine').click()
            assert page.locator('.gallery-navigation').is_hidden()
            assert page.locator('.segment-toolbar').is_hidden()
            assert page.locator('.account-toolbar #disconnect').is_visible()
            page.locator('#tabDiscovery').click()
            assert page.locator('.gallery-navigation').is_hidden()
            page.locator('#tabGallery').click()
            assert page.locator('.gallery-navigation').is_hidden()
            assert page.locator('.segment-toolbar').is_visible()

            calendar = page.locator('#calendarToggle')
            calendar.focus()
            calendar.press('ArrowDown')
            page.locator('#calendarPanel [aria-current="date"]').wait_for()
            assert calendar.get_attribute('aria-expanded') == 'true'
            assert page.locator('#calendarPanel .calendar-grid button:disabled').count() > 0
            page.locator('#calendarPanel [data-segment="morning"]').click()
            page.locator('#calendarToggle', has_text='Morning').wait_for()
            page.locator('.creator-card').first.wait_for()
            page.wait_for_timeout(100)
            assert page.locator('#calendarPanel').is_hidden()
            calendar.press('Enter')
            calendar.press('Escape')
            assert page.locator('#calendarPanel').is_hidden()
            assert calendar.evaluate('node => node === document.activeElement')

            opener = page.locator('.info-button').first
            opener.focus()
            page.evaluate("document.querySelector('#details').showModal()")
            assert page.locator('#details').get_attribute('aria-labelledby') == 'detailCreator'
            page.keyboard.press('Escape')
            assert page.locator('#details').is_hidden()
            assert opener.evaluate('node => node === document.activeElement')
            page.evaluate("document.querySelector('#details').showModal()")
            page.locator('#details').click(position={'x': 4, 'y': 4})
            assert page.locator('#details').is_visible(), 'inside padding is not a backdrop click'
            page.mouse.click(0, 0)
            assert page.locator('#details').is_hidden()

            page.locator('#filterToggle').click()
            page.locator('#filterPanel .model-search').wait_for()
            assert page.locator('#modelMenu .filter-row:visible').count() <= 8
            page.locator('#filterPanel .model-search').fill('flux')
            assert page.locator('#filterPanel .model-search').input_value() == 'flux'
            visible_models = page.locator('#modelMenu .filter-row:visible')
            assert visible_models.count() == 1
            page.locator('#filterPanel .model-search').fill('')
            page.locator('#modelMenu input').nth(0).check()
            page.locator('#modelMenu input').nth(1).check()
            page.locator('#filterToggle[data-active-count="2"]').wait_for()
            assert page.locator('.model-chip').count() == 2
            panel_bounds = page.locator('#filterPanel').bounding_box()
            assert panel_bounds['y'] >= 0 and panel_bounds['y'] + panel_bounds['height'] <= 844
            page.get_by_role('button', name='Close filters', exact=True).click()
            page.get_by_role('button', name='Remove model filter SDXL', exact=True).click()
            page.locator('#filterToggle[data-active-count="1"]').wait_for()
            page.locator('#filterToggle').click()
            page.get_by_role('button', name='Reset filters', exact=True).click()
            page.locator('#filterToggle[data-active-count="0"]').wait_for()
            assert page.locator('.model-chip').count() == 0
            page.locator('#contentMenu [data-level="2"]').click()
            page.locator('#filterToggle[data-active-count="1"]').wait_for()
            page.wait_for_selector('#contentFilter:enabled')
            page.get_by_role('button', name='Reset filters', exact=True).click()
            page.locator('#filterToggle[data-active-count="0"]').wait_for()
            page.get_by_role('button', name='Close filters', exact=True).click()
            assert page.locator('#contentFilter').inner_text() == 'Content: PG + PG-13'

            page.route('**/api/probe', lambda route: route.fulfill(content_type='application/json',
                body='{"custom":"' + route.request.headers.get('x-ui-test', '') + '"}'))
            page.route('**/api/probe-error', lambda route: route.fulfill(status=409,
                content_type='application/json', body='{"error":"Try again"}'))
            transport = page.evaluate("""async () => {
                const { api } = await import('/ui/api.js');
                const value = await api('/api/probe', {headers: {'X-UI-Test': 'preserved'}});
                let error, aborted;
                try { await api('/api/probe-error'); } catch (e) { error = e.message; }
                const controller = new AbortController(); controller.abort();
                try { await api('/api/probe', {signal: controller.signal}); }
                catch (e) { aborted = e.name; }
                return {value, error, aborted};
            }""")
            assert transport == {'value': {'custom': 'preserved'}, 'error': 'Try again', 'aborted': 'AbortError'}, transport

            sizing = page.evaluate("""async () => {
                const {cardPreviewUrl} = await import('/ui/artwork.js');
                const source = 'https://image.civitai.com/key/id/width=768/example.jpg';
                return {
                    standard: cardPreviewUrl(source, 400, 1),
                    retina: cardPreviewUrl(source, 400, 2),
                    compact: cardPreviewUrl(source, 250, 1),
                    original: cardPreviewUrl(source.replace('width=768', 'original=true'), 400, 1),
                    foreign: cardPreviewUrl('https://example.com/width=768/image.jpg', 400, 1),
                    local: cardPreviewUrl('/width=768/image.jpg', 400, 1),
                };
            }""")
            assert '/width=512/' in sizing['standard'], sizing
            assert '/width=1024/' in sizing['retina'], sizing
            assert '/width=384/' in sizing['compact'], sizing
            assert '/original=true/' in sizing['original'], sizing
            assert sizing['foreign'] == 'https://example.com/width=768/image.jpg'
            assert sizing['local'] == '/width=768/image.jpg'

            page.route('**/missing-preview.svg', lambda route: route.fulfill(status=404))
            page.route('**/original-preview.svg', lambda route: route.fulfill(content_type='image/svg+xml',
                body='<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>'))
            fallback = page.evaluate("""async () => {
                const {showArtwork} = await import('/ui/artwork.js');
                const image = document.createElement('img'); document.body.append(image);
                await new Promise(resolve => {
                    image.addEventListener('load', () => requestAnimationFrame(resolve), {once:true});
                    showArtwork(image, '/missing-preview.svg', '/original-preview.svg');
                });
                const observer = new MutationObserver(() => {});
                observer.observe(image, {attributes:true, attributeFilter:['src']});
                showArtwork(image, '/missing-preview.svg', '/original-preview.svg');
                const result = {src:image.getAttribute('src'), rewrites:observer.takeRecords().length,
                    pending:image.classList.contains('image-pending'), width:image.naturalWidth};
                observer.disconnect(); image.remove(); return result;
            }""")
            assert fallback == {'src': '/original-preview.svg', 'rewrites': 0, 'pending': False, 'width': 8}, fallback

            # Known cached decisions bypass the verification request; unknown images
            # still take that path, and a cached hidden card never requests artwork.
            cards = [creator(i) for i in (101, 102, 103)]
            cards[0]['representative']['tagState'] = {'known': True, 'tags': []}
            cards[1]['representative']['tagState'] = {'known': True,
                'tags': [{'name': 'blocked', 'hidden': True}]}
            page.route('**/api/history/artists?*', lambda route: route.fulfill(
                content_type='application/json', body=json.dumps({'artists': cards, 'total': 3})))
            checked = []
            requests = []

            def tags(route):
                ids = json.loads(route.request.post_data)['imageIds']
                checked.extend(ids)
                route.fulfill(content_type='application/json', body=json.dumps(
                    {'images': {str(i): {'known': True, 'tags': []} for i in ids}}))

            page.route('**/api/history/tags', tags)
            page.on('request', lambda request: requests.append(request.url))
            page.set_viewport_size({'width': 1920, 'height': 1080})
            page.reload(wait_until='networkidle')
            page.locator('.creator-card[data-id="103"] img[src]').first.wait_for()
            assert 101 not in checked and 102 not in checked and 103 in checked, checked
            assert not any('/fixture/102.svg' in url for url in requests), requests
            assert page.locator('.creator-card[data-id="102"]').count() == 0

            held = []

            def slow_page(route):
                view = parse_qs(urlparse(route.request.url).query).get('view', [''])[0]
                if view == 'new':
                    held.append(route)
                else:
                    route.fulfill(content_type='application/json', body=json.dumps(
                        {'artists': [creator(111)], 'total': 1}))

            page.route('**/api/history/artists?*', slow_page)
            with page.expect_request('**/api/history/artists?*'):
                page.locator('#dayView').select_option('new')
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(10)
            assert held, 'slow response was not held'
            assert page.locator('.gallery-skeleton').count() > 0
            page.locator('#dayView').select_option('discovery')
            # The stale response is still held: the new card must arrive independently.
            page.locator('.creator-card[data-id="111"]').wait_for(timeout=3000)
            assert page.locator('.creator-card').count() == 1
            assert page.locator('.gallery-skeleton').count() == 0
            for route in held:
                route.abort()

            failing = [True]

            def retry_page(route):
                if failing[0]:
                    failing[0] = False
                    route.fulfill(status=503, content_type='application/json', body='{"error":"Temporarily unavailable"}')
                else:
                    route.fulfill(content_type='application/json', body=json.dumps(
                        {'artists': [creator(114)], 'total': 1, 'hasMore': False}))

            page.route('**/api/history/artists?*', retry_page)
            page.locator('#dayView').select_option('new')
            page.get_by_role('button', name='Retry loading artists', exact=True).click()
            page.locator('.creator-card[data-id="114"]').wait_for()
            page.get_by_text("You're caught up for this view.", exact=True).wait_for()
            page.route('**/api/history/artists?*', lambda route: route.fulfill(
                content_type='application/json', body='{"artists":[],"total":0,"hasMore":false}'))
            page.locator('#dayView').select_option('discovery')
            page.get_by_text('No creators match these filters.', exact=True).wait_for()
            assert page.locator('.creator-card').count() == 0
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print({'shellKeyboard': True, 'sharedTransport': True, 'fallbackSurvivesRepaint': True,
           'filterChipsAndReset': True})


if __name__ == '__main__':
    run()

"""Controlled missing and transient media across real gallery adapters and browsers."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from functools import partial
from http.server import ThreadingHTTPServer
import json
import os
import threading
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT, StaticHandler, creator, install_fixture

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="120"><rect width="80" height="120" fill="green"/></svg>'
server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(ROOT / 'static')))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = os.environ.get('CIVITAI_UI_BASE', f'http://127.0.0.1:{server.server_port}')
tested = []


def artwork(i):
    item = dict(creator(i)['representative'])
    item['url'] = f'https://image.civitai.com/unavailable/{i}.jpeg'
    item['thumbnailUrl'] = f'https://image.civitai.com/unavailable/preview/{i}.jpeg'
    if i == 1003:
        item.pop('thumbnailUrl')  # Older archives need only an original URL.
    if i == 2:
        item['availabilitySources']=[item['thumbnailUrl'],item['url']]
        item['url']=item['thumbnailUrl']='/cached-unavailable/2.jpeg'
    return item


try:
    with sync_playwright() as p:
        for name, options in [('Chromium', {}), ('Edge', {'channel': 'msedge'})]:
            browser = p.chromium.launch(headless=True, **options)
            for surface in ['daily', 'time-machine', 'historical-creator']:
                page = browser.new_page(viewport={'width': 1450, 'height': 1000})
                page.context.new_cdp_session(page).send('Network.setCacheDisabled', {'cacheDisabled': True})
                install_fixture(page, base, cached_tags=True)
                errors, probes, offsets, mutations = [], [], [], []
                page.on('pageerror', lambda e: errors.append(str(e)))
                cards = [creator(i) for i in range(1, 26)]
                for c in cards:
                    c['representative'] = artwork(c['representative']['id'])
                cards[2]['representative'] = artwork(121513586)
                cards[3]['representative'] = artwork(23155956)
                lists = {'Studio_001': [artwork(i) for i in [1, 1001, 1002, 1003]],
                         'Studio_002': [artwork(i) for i in [2, 2002]]}
                if surface == 'daily':
                    cards[0]['imageCount'] = 4
                    cards[1]['imageCount'] = 2
                missing = {1, 2, 1002, 2002, 121513586, 23155956}
                requests = {}

                def route(r):
                    url = urlsplit(r.request.url)
                    path = url.path
                    query = parse_qs(url.query)
                    if url.hostname == 'image.civitai.com':
                        i = int(path.rsplit('/', 1)[-1].split('.')[0])
                        requests[path] = requests.get(path, 0) + 1
                        status = (410 if i == 2002 else 404) if i in missing or (i == 6 and '/preview/' in path) else 200
                        if i == 5 and requests[path] == 1:
                            status = 429
                        if i == 7 and requests[path] == 1:
                            r.abort('timedout'); return
                        if i == 8:
                            status = 403
                        if i == 9:
                            status = 503
                        r.fulfill(status=status, content_type='image/svg+xml', body=SVG if status == 200 else 'Missing')
                        return
                    data = None
                    if path == '/api/history/artists':
                        offset = int(query['offset'][0]); offsets.append(offset)
                        limit = int(query['limit'][0])
                        data = {'artists': cards[offset:offset+limit], 'total': 25, 'hasMore': offset+limit < 25}
                    elif path == '/api/history/artist':
                        data = {'images': lists[query['username'][0]]}
                    elif path == '/api/timemachine':
                        data = {'cards': cards[:8], 'status': {'creators': 8, 'primed': 8}}
                    elif path == '/api/timemachine/creator':
                        username = query['username'][0]
                        items = lists.get(username, [next(c['representative'] for c in cards if c['username'] == username)])
                        items = [dict(item, position=n) for n, item in enumerate(items)]
                        entries = [dict(cards[0], username=username, imageCount=1, representative=item) for item in items]
                        data = {'images': items, 'cards': entries, 'hasMore': False, 'username': username}
                    elif path == '/api/artwork/availability':
                        body = json.loads(r.request.post_data); probes.append(body)
                        statuses = [(410 if int(u.rsplit('/', 1)[-1].split('.')[0]) == 2002 else 404)
                                    if int(u.rsplit('/', 1)[-1].split('.')[0]) in missing else 429 for u in body['sources']]
                        data = {'unavailable': all(s in (404, 410) for s in statuses), 'statuses': statuses}
                    elif path in ['/api/timemachine/seen', '/api/timemachine/advance']:
                        mutations.append(path); data = {'advanced': 0}
                    elif path == '/api/history/image':
                        item = artwork(int(query['id'][0])); item['detailImageUrl'] = item['thumbnailUrl']
                        data = item
                    if data is None:
                        r.fallback()
                    else:
                        r.fulfill(content_type='application/json', body=json.dumps(data))

                page.route('**/*', route)
                page.goto(base, wait_until='domcontentloaded')
                if surface != 'daily':
                    page.locator('#tabTimeMachine').click()
                    if surface == 'historical-creator':
                        # Use the production creator-history adapter directly, without scrolling/seen writes.
                        page.evaluate("() => openTimeMachineCreator('Studio_001')")
                grid = '#gallery' if surface == 'daily' else '#timeMachineCreatorGrid' if surface == 'historical-creator' else '#timeMachineGrid'
                page.wait_for_function("selector => document.querySelector(`${selector} [data-username='studio_001']`)?.dataset.id === '1001'", arg=grid)
                first = page.locator(grid + ' [data-username="studio_001"]')
                if surface != 'historical-creator':
                    page.wait_for_function("selector => !document.querySelector(`${selector} [data-username='studio_002']`)", arg=grid)
                    page.wait_for_function("selector => !document.querySelector(`${selector} [data-id='121513586'],${selector} [data-id='23155956']`)", arg=grid)
                    page.wait_for_timeout(1800)
                    assert page.locator(grid + ' [data-id="5"] img').evaluate('(e)=>e.naturalWidth') > 0
                    assert page.locator(grid + ' [data-id="6"] img').evaluate('(e)=>e.naturalWidth') > 0
                    assert page.locator(grid + ' [data-id="7"] img').evaluate('(e)=>e.naturalWidth') > 0
                    assert page.locator(grid + ' [data-id="8"]').count() == 1
                    if surface == 'daily':
                        a = first.bounding_box()
                        b = page.locator(grid + ' [data-id="5"]').bounding_box()
                        assert abs(a['y'] - b['y']) < 2 and b['x'] > a['x'], (a, b)
                    first.focus()
                    page.wait_for_function("s=>document.querySelector(s).getAttribute('aria-busy')==='false'", arg=grid+' [data-username="studio_001"]')
                    page.keyboard.press('ArrowRight')
                    page.wait_for_function("s=>document.querySelector(s)?.dataset.id==='1003'", arg=grid+' [data-username="studio_001"]')
                    assert first.locator('.image-position').inner_text() == '2 of 2 images'
                    page.wait_for_function("s=>document.querySelector(s).getAttribute('aria-busy')==='false'", arg=grid+' [data-username="studio_001"]')
                    page.locator('body').click(position={'x': 5, 'y': 170})
                    first.hover(); page.keyboard.press('ArrowLeft')
                    page.wait_for_function("s=>document.querySelector(s)?.dataset.id==='1001'", arg=grid+' [data-username="studio_001"]')
                    assert first.locator('.image-position').inner_text() == '1 of 2 images'
                    # An uncached detail selection fails through the shared path,
                    # closes gracefully, and leaves usable card artwork intact.
                    page.wait_for_function("s=>document.querySelector(s).getAttribute('aria-busy')==='false'", arg=grid+' [data-username="studio_001"]')
                    missing.add(9003)
                    page.evaluate("item=>showDetails(item,{username:'DetailFixture',imageCount:1})", artwork(9003))
                    page.wait_for_function("()=>!document.querySelector('#details').open")
                    assert first.locator('.image-position').inner_text() == '1 of 2 images'
                if surface == 'daily':
                    page.locator('#loadSentinel').scroll_into_view_if_needed()
                    page.wait_for_function("()=>!!document.querySelector('#gallery [data-id=\"25\"]')")
                    assert offsets == [0, 24], offsets
                    assert page.locator('#gallery [data-username="studio_001"]').count() == 1
                    failed_reads = requests['/unavailable/1.jpeg']
                    page.evaluate('()=>window.scrollTo(0,0)')
                    page.evaluate('()=>reloadView()')
                    page.wait_for_function("()=>document.querySelector('#gallery [data-username=\"studio_001\"]')?.dataset.id==='1001'")
                    assert requests['/unavailable/1.jpeg'] == failed_reads
                    assert first.locator('.image-position').inner_text() == '1 of 2 images'
                assert not errors, errors
                assert len(probes) < 20, probes
                # No failure handler writes seen/advance. No scrolling occurred in TM.
                if surface == 'time-machine':
                    assert not mutations, mutations
                tested.append(name + ':' + surface)
                page.close()
            # A late confirmation for an old source cannot suppress its successor.
            page = browser.new_page()
            page.context.new_cdp_session(page).send('Network.setCacheDisabled', {'cacheDisabled': True})
            install_fixture(page, base)
            held = []
            page.route('**/api/artwork/availability', lambda r: held.append(r))
            page.route('https://image.civitai.com/race/old.jpeg', lambda r: r.fulfill(status=404, body='Missing'))
            page.route('https://image.civitai.com/race/new.jpeg', lambda r: r.fulfill(content_type='image/svg+xml', body=SVG))
            page.goto(base, wait_until='domcontentloaded')
            page.evaluate('''async () => {
                const m = await import('/ui/artwork.js'); window.availabilityModule=m;
                window.raceImage = new Image(); document.body.prepend(raceImage);
                window.missingEvents=0; raceImage.addEventListener('artwork-unavailable',()=>missingEvents++);
                m.showArtwork(raceImage, '', 'https://image.civitai.com/race/old.jpeg', {id:9001});
            }''')
            for _ in range(100):
                page.wait_for_timeout(30)
                if held:
                    break
            assert held
            page.evaluate("()=>availabilityModule.showArtwork(raceImage,'','https://image.civitai.com/race/new.jpeg',{id:9002})")
            held.pop().fulfill(content_type='application/json', body='{"unavailable":true,"statuses":[404]}')
            page.wait_for_function('()=>raceImage.naturalWidth>0')
            page.evaluate("()=>raceImage.dispatchEvent(new Event('error'))")
            page.wait_for_timeout(100)
            assert page.evaluate('()=>missingEvents') == 0
            assert not page.evaluate("()=>availabilityModule.isArtworkUnavailable({id:9001,url:'https://image.civitai.com/race/old.jpeg'})")
            page.evaluate('''() => {
                window.raceImage2 = new Image(); document.body.prepend(raceImage2);
                raceImage2.addEventListener('artwork-unavailable',()=>missingEvents++);
                for (const img of [raceImage,raceImage2]) availabilityModule.showArtwork(
                    img,'','https://image.civitai.com/race/old.jpeg',{id:9010});
            }''')
            for _ in range(100):
                page.wait_for_timeout(30)
                if held:
                    break
            assert len(held) == 1, 'Identical artwork should share its status check'
            held.pop().fulfill(content_type='application/json', body='{"unavailable":true,"statuses":[410]}')
            page.wait_for_function('()=>missingEvents===2')
            page.evaluate("()=>availabilityModule.showArtwork(raceImage,'','https://image.civitai.com/race/new.jpeg',{id:9010})")
            page.wait_for_function('()=>raceImage.naturalWidth>0')
            assert not page.evaluate("()=>availabilityModule.isArtworkUnavailable({id:9010,url:'https://image.civitai.com/race/new.jpeg'})")
            page.evaluate("()=>availabilityModule.showArtwork(raceImage2,'','https://image.civitai.com/race/old.jpeg',{id:9020})")
            for _ in range(100):
                page.wait_for_timeout(30)
                if held:
                    break
            assert held
            page.evaluate('()=>raceImage2.remove()')
            held.pop().fulfill(content_type='application/json', body='{"unavailable":true,"statuses":[404]}')
            page.wait_for_timeout(100)
            assert not page.evaluate("()=>availabilityModule.isArtworkUnavailable({id:9020,url:'https://image.civitai.com/race/old.jpeg'})")
            assert page.evaluate('()=>missingEvents') == 2
            page.close()
            tested.append(name + ':stale-source')
            browser.close()
finally:
    server.shutdown(); server.server_close()
print({'passed': tested})

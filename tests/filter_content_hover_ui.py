"""Real pointer/keyboard regressions for Content, Filters, and carousel ownership."""
import test_safety  # Refuse live application endpoints and isolate all test data.

from datetime import datetime, timedelta
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DAY = (datetime.now() - timedelta(days=1)).date().isoformat()
MEDIA = (ROOT / 'tests/fixtures/recovery.mp4').read_bytes()
tested = []


def get(base, path):
    return json.load(urllib.request.urlopen(base + path, timeout=10))


def wait_ready(page):
    page.wait_for_function("() => [...document.querySelectorAll('#gallery .creator-card')].every(c=>c.getAttribute('aria-busy')==='false')")


def step(page, card, key):
    old = card.get_attribute('data-id')
    page.keyboard.press(key)
    page.wait_for_function("old => document.querySelector(`[data-username='${old.name}']`)?.dataset.id!==old.id",
                           arg={'name': card.get_attribute('data-username'), 'id': old})
    wait_ready(page)
    return card.get_attribute('data-id')


with tempfile.TemporaryDirectory(prefix='cad3-content-hover-', ignore_cleanup_errors=True) as temporary:
    os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
    from discovery.history import HistoryArchive
    from discovery.settings import AppSettings

    settings = AppSettings(Path(temporary) / 'settings.json')
    settings.update(browsing_levels_value=[1, 2, 4], auto_capture_value=False)
    archive = HistoryArchive(Path(temporary) / 'history', selected_browsing_levels=[1, 2, 4])
    records = [(10, 'Alpha', 1, 'image', 'Foo'), (11, 'Alpha', 2, 'video', 'Foo'),
               (12, 'Alpha', 4, 'image', 'Bar'), (13, 'Alpha', 1, 'image', 'Foo'),
               (20, 'Beta', 1, 'image', 'Foo'), (21, 'Beta', 2, 'image', 'Foo'),
               (22, 'Beta', 4, 'video', 'Bar'), (30, 'Gamma', 4, 'image', 'Bar')]
    archive._upsert_normalized([{
        'id': i, 'username': name, 'type': kind, 'baseModel': model,
        'browsingLevel': level, 'createdAt': f'{DAY}T13:00:{i:02}Z',
        'url': f'https://image.civitai.com/ui-test/{i}/original=true/{i}.{"mp4" if kind == "video" else "jpeg"}',
        'width': 320, 'height': 400, 'stats': {'reactionCount': 200-i},
    } for i, name, level, kind, model in records], forced_date=DAY)
    with archive.connect() as db:
        db.execute('INSERT INTO days(day,complete,content_rating) VALUES(?,1,?)', (DAY, 'Mature'))
    archive.build_artist_index(DAY)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(port), '--no-browser'],
                               cwd=ROOT, env={**os.environ, 'CIVITAI_HISTORY_DATA_DIR': temporary},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                get(base, '/api/history/config')
                break
            except OSError:
                time.sleep(.1)
        with sync_playwright() as p:
            for name, options in [('Chromium', {}), ('Edge', {'channel': 'msedge'})]:
                try:
                    browser = p.chromium.launch(headless=True, **options)
                except Exception:
                    if name == 'Chromium':
                        raise
                    continue
                settings.update(browsing_levels_value=[1, 2, 4], media_type_value='all')
                page = browser.new_page(viewport={'width': 1450, 'height': 1000},
                                        reduced_motion='reduce', bypass_csp=True)
                errors, model_reads = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: model_reads.append(request.url)
                        if '/api/history/models?' in request.url else None)

                def route(r):
                    path = r.request.url
                    if path.startswith('https://image.civitai.com/'):
                        if r.request.resource_type == 'media':
                            match = re.match(r'bytes=(\d+)-(\d*)', r.request.headers.get('range', ''))
                            start = int(match[1]) if match else 0
                            end = int(match[2]) if match and match[2] else len(MEDIA)-1
                            headers = {'Accept-Ranges': 'bytes'}
                            if match:
                                headers['Content-Range'] = f'bytes {start}-{end}/{len(MEDIA)}'
                            r.fulfill(status=206 if match else 200, content_type='video/mp4',
                                      headers=headers, body=MEDIA[start:end+1])
                        else:
                            r.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="320" height="400"><rect width="320" height="400" fill="#345"/></svg>')
                    elif path.endswith('/api/auth-status'):
                        r.fulfill(json={'connected': True, 'username': 'fixture', 'socialWrite': False})
                    elif path.endswith('/api/discovery/summary'):
                        r.fulfill(json={'hasData': True, 'lastSyncAt': datetime.now().astimezone().isoformat()})
                    elif '/api/history/tags' in path:
                        ids = json.loads(r.request.post_data)['imageIds']
                        r.fulfill(json={'images': {str(i): {'known': True, 'tags': []} for i in ids}})
                    elif '/api/reaction-status?' in path:
                        r.fulfill(json={'images': {}})
                    else:
                        r.continue_()

                page.route('**/*', route)
                page.goto(base, wait_until='domcontentloaded')
                try:page.wait_for_selector('#gallery .creator-card')
                except Exception:
                    raise AssertionError({'browser':name,'pageErrors':errors,
                        'status':get(base,f'/api/history/status?date={DAY}&segment=all'),
                        'serverError':(Path(temporary)/'error.log').read_text(encoding='utf-8') if (Path(temporary)/'error.log').exists() else None,
                        'page':page.locator('main').inner_text()[:1500]})
                wait_ready(page)
                content, filters = page.locator('#contentFilter'), page.locator('#filterToggle')
                compact, full = page.locator('#contentPanel'), page.locator('#filterPanel')
                assert get(base, '/api/settings')['browsingLevels'] == [1, 2, 4]
                count_before = len(model_reads)
                for _ in range(2):
                    content.click()
                    assert compact.is_visible() and not full.is_visible()
                    assert compact.locator('[data-level]').count() == 5
                    assert compact.locator('[aria-pressed="true"]').count() == 3
                    assert compact.locator('select,input,#modelMenu').count() == 0
                    assert content.get_attribute('aria-expanded') == 'true'
                    content.click()
                    assert not compact.is_visible()
                    assert content.get_attribute('aria-expanded') == 'false'
                assert len(model_reads) == count_before, 'Content opening fetched model metadata'
                content.click()
                page.mouse.click(10, 90)
                assert not compact.is_visible()
                content.focus()
                page.keyboard.press('ArrowDown')
                assert compact.locator('[data-level="1"]').evaluate('(el)=>el===document.activeElement')
                page.keyboard.press('Escape')
                assert not compact.is_visible() and content.evaluate('(el)=>el===document.activeElement')
                page.keyboard.press('Enter')
                assert compact.is_visible()
                page.keyboard.press('Enter')
                assert not compact.is_visible()
                content.press('ArrowDown')
                page.keyboard.press('Space')
                page.wait_for_function("() => !document.querySelector('#contentFilter').disabled && !galleryFilterState().levels.includes(1)")
                page.keyboard.press('Escape')
                content.click()
                compact.locator('[data-level="1"]').click()
                page.wait_for_function("() => !document.querySelector('#contentFilter').disabled && galleryFilterState().levels.length===3")
                page.keyboard.press('Escape')

                content.click()
                compact.locator('[data-level="4"]').click()
                page.wait_for_function("() => !document.querySelector('#contentFilter').disabled && galleryFilterState().levels.length===2")
                assert get(base, '/api/settings')['browsingLevels'] == [1, 2]
                page.wait_for_function("() => document.querySelectorAll('#gallery .creator-card').length===2")
                assert page.locator('[data-username="gamma"]').count() == 0
                filters.click()
                assert full.is_visible() and not compact.is_visible()
                assert full.locator('[data-level="4"]').get_attribute('aria-pressed') == 'false'
                full.locator('[data-level="4"]').click()
                page.wait_for_function("() => !document.querySelector('#contentFilter').disabled && galleryFilterState().levels.length===3")
                content.click()
                assert compact.is_visible() and not full.is_visible()
                assert compact.locator('[data-level="4"]').get_attribute('aria-pressed') == 'true'
                page.locator('#calendarToggle').click()
                assert not compact.is_visible()
                page.wait_for_selector('#calendarPanel:not(.hidden)')
                content.focus()
                page.keyboard.press('ArrowDown')
                assert compact.is_visible() and page.locator('#calendarPanel').is_hidden()
                page.locator('#galleryPreferences').click()
                page.wait_for_selector('#preferencesMenu:not(.hidden)')
                assert not compact.is_visible()
                content.click()
                assert page.locator('#preferencesMenu').is_hidden()
                page.keyboard.press('Escape')
                filters.click()
                filters.click()
                assert not full.is_visible()
                filters.focus()
                page.keyboard.press('ArrowDown')
                assert full.is_visible()
                page.keyboard.press('Escape')
                assert filters.evaluate('(el)=>el===document.activeElement')

                for width, height in [(800, 900), (390, 844), (1450, 1000)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    content.click()
                    page.wait_for_timeout(50)
                    bounds = compact.bounding_box()
                    assert bounds['x'] >= 11 and bounds['x']+bounds['width'] <= width-11, bounds
                    assert bounds['y'] >= 11 and bounds['y']+bounds['height'] <= height-11, bounds
                    page.keyboard.press('Escape')

                filters.click()
                full.locator('#mediaType').select_option('video')
                page.wait_for_function("() => document.querySelectorAll('#gallery .creator-card').length===2 && galleryFilterState().media==='video'")
                full.locator('#modelMenu input[value="Foo"]').check()
                page.wait_for_function("() => document.querySelectorAll('#gallery .creator-card').length===1")
                single = page.locator('#gallery .creator-card')
                assert single.locator('.next').is_hidden()
                page.keyboard.press('Escape')
                single.hover()
                page.evaluate('document.activeElement.blur()')
                page.keyboard.press('ArrowRight')
                assert single.get_attribute('data-id') == '11'
                filters.click()
                full.locator('.filter-reset').click()
                page.wait_for_function("() => !document.querySelector('#contentFilter').disabled && galleryFilterState().media==='all' && galleryFilterState().models.length===0 && galleryFilterState().levels.length===2")
                assert get(base, '/api/settings')['browsingLevels'] == [1, 2]
                assert get(base, '/api/settings')['mediaType'] == 'all'
                page.keyboard.press('Escape')
                wait_ready(page)

                alpha = page.locator('#gallery [data-username="alpha"]')
                beta = page.locator('#gallery [data-username="beta"]')
                first, second = alpha.get_attribute('data-id'), beta.get_attribute('data-id')
                alpha.hover()
                page.evaluate('document.activeElement.blur()')
                assert step(page, alpha, 'ArrowRight') != first
                assert step(page, alpha, 'ArrowLeft') == first
                beta.hover()
                assert step(page, beta, 'ArrowRight') != second
                assert step(page, beta, 'ArrowLeft') == second
                page.mouse.move(0, 0)
                page.keyboard.press('ArrowRight')
                assert alpha.get_attribute('data-id') == first and beta.get_attribute('data-id') == second
                alpha.focus()
                assert step(page, alpha, 'ArrowRight') != first
                assert step(page, alpha, 'ArrowLeft') == first
                alpha.focus()
                beta.hover()
                assert step(page, alpha, 'ArrowRight') != first
                assert beta.get_attribute('data-id') == second
                step(page, alpha, 'ArrowLeft')
                alpha.locator('.more-menu').focus()
                assert step(page, beta, 'ArrowRight') != second
                assert step(page, beta, 'ArrowLeft') == second
                assert alpha.get_attribute('data-id') == first

                # Native inputs and controls win even with an eligible hovered card.
                page.evaluate("""() => {
                    const host=document.createElement('div');host.id='nativeInputs';
                    host.innerHTML='<input id="textInput" value="abc"><textarea>abc</textarea><select><option>One</option><option>Two</option></select><input type="range" min="0" max="10" value="4"><div contenteditable="true">abc</div><video controls tabindex="0"></video>';
                    document.body.append(host);host.querySelector('video').src='https://image.civitai.com/ui-test/native.mp4';
                }""")
                for selector in ['#textInput', '#nativeInputs textarea', '#nativeInputs select',
                                 '#nativeInputs input[type="range"]', '#nativeInputs [contenteditable]']:
                    page.locator(selector).focus()
                    page.keyboard.press('ArrowRight')
                    assert beta.get_attribute('data-id') == second
                assert page.locator('#nativeInputs input[type="range"]').input_value() == '5'
                page.wait_for_function("() => document.querySelector('#nativeInputs video').readyState>=2")
                page.evaluate("() => {const v=document.querySelector('#nativeInputs video');v.pause();v.currentTime=2}")
                page.wait_for_function("() => {const v=document.querySelector('#nativeInputs video');return !v.seeking && v.currentTime>1.9}")
                page.locator('#nativeInputs video').focus()
                page.keyboard.press('ArrowRight')
                try:
                    page.wait_for_function("() => document.querySelector('#nativeInputs video').currentTime>2.1", timeout=3000)
                except Exception:
                    print(page.evaluate("() => {const v=document.querySelector('#nativeInputs video');return {active:document.activeElement.tagName,time:v.currentTime,seeking:v.seeking,ready:v.readyState,seekable:v.seekable.length,paused:v.paused}}"), flush=True)
                    raise
                assert beta.get_attribute('data-id') == second
                page.locator('#nativeInputs').evaluate('(el)=>el.remove()')
                alpha.focus()
                for modifier in ['Control', 'Alt', 'Shift', 'Meta']:
                    page.keyboard.press(modifier+'+ArrowRight')
                    assert alpha.get_attribute('data-id') == first
                page.evaluate("() => {const d=document.createElement('dialog');d.id='keyDialog';d.textContent='Modal';document.body.append(d);d.showModal()}")
                page.keyboard.press('ArrowRight')
                assert alpha.get_attribute('data-id') == first
                page.keyboard.press('Escape')
                page.locator('#keyDialog').evaluate('(el)=>el.remove()')

                # Existing focused Space behavior still plays/cancels real native video.
                alpha.focus()
                for _ in range(3):
                    if alpha.get_attribute('data-id') == '11':
                        break
                    step(page, alpha, 'ArrowRight')
                assert alpha.get_attribute('data-id') == '11'
                page.keyboard.press('Space')
                page.wait_for_function("() => document.querySelector('[data-username=alpha] video')?.currentTime>0.1")
                page.keyboard.press('Space')
                assert alpha.locator('video').count() == 0

                # Detached and CSS-hidden hover owners cannot receive later navigation.
                page.evaluate('document.activeElement.blur()')
                beta.hover()
                page.evaluate("window.staleCard=document.querySelector('[data-username=beta]');staleCard.remove()")
                current = alpha.get_attribute('data-id')
                page.keyboard.press('ArrowRight')
                assert alpha.get_attribute('data-id') == current
                alpha.hover()
                alpha.evaluate('(el)=>el.hidden=true')
                page.keyboard.press('ArrowRight')
                assert alpha.get_attribute('data-id') == current
                alpha.evaluate('(el)=>el.hidden=false')
                alpha.hover()
                alpha.evaluate("(el)=>el.style.visibility='hidden'")
                page.keyboard.press('ArrowRight')
                assert alpha.get_attribute('data-id') == current
                alpha.evaluate("(el)=>el.style.visibility='visible'")
                alpha.hover()
                filters.click()
                full.locator('#mediaType').select_option('image')
                page.wait_for_function("() => galleryFilterState().media==='image' && document.querySelectorAll('#gallery .creator-card').length===2")
                page.keyboard.press('Escape')
                wait_ready(page)
                ids = page.locator('#gallery .creator-card').evaluate_all('(els)=>els.map(el=>el.dataset.id)')
                page.evaluate('document.activeElement.blur()')
                page.keyboard.press('ArrowRight')
                assert page.locator('#gallery .creator-card').evaluate_all('(els)=>els.map(el=>el.dataset.id)') == ids
                assert not errors, errors
                browser.close()
                tested.append(name)
    finally:
        process.terminate()
        process.wait(timeout=15)

print({'browsers': tested, 'contentToggle': True, 'separateSynchronizedRatings': True,
       'ratingAndCombinedFilters': True, 'resetPreserved': True, 'hoverAndFocusPriority': True,
       'nativeControlsAndSeeking': True, 'modifierAndModalGuards': True,
       'detachedHiddenReplacedGuards': True, 'nativeVideoSpace': True, 'viewportSizes': 3})

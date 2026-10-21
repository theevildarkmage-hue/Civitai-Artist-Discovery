"""A delayed calendar summary must not hide an already-started build's Stop control."""
import test_safety
from datetime import datetime,timedelta
import json
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as probe:
    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(extra_http_headers=h.headers())
        held=[]; delaying=False; started=False
        def reply(route,value):route.fulfill(content_type='application/json',body=json.dumps(value))
        page.route('**/api/auth-status',lambda route:reply(route,{'connected':True,'id':42,'username':'MockAccount'}))
        page.route('**/api/discovery/summary',lambda route:reply(route,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
        def blocks(route):
            if delaying:held.append(route)
            else:reply(route,{'blocks':{}})
        page.route('**/api/history/blocks?*',blocks)
        def status(route):reply(route,{'complete':False,'state':'loading' if started else 'not_started',
            'phase':'collecting','itemCount':12,'creatorCount':3,'elapsedSeconds':2})
        page.route('**/api/history/status?*',status)
        page.route('**/api/history/start',status)
        page.route('**/api/history/cancel',lambda route:reply(route,{'cancelled':True}))
        page.goto(h.base,wait_until='networkidle');page.locator('#tabGallery').click()
        page.locator('#startLoading').wait_for()
        delaying=True
        day=(datetime.now().date()-timedelta(days=2)).isoformat()
        page.evaluate('day=>{window.delayedSelection=window.selectGalleryDate(day,"morning")}',day)
        for _ in range(50):
            if held:break
            page.wait_for_timeout(20)
        assert len(held)==1
        started=True
        page.evaluate('()=>{window.pendingBuild=beginSelectedDay()}')
        page.locator('#phaseCollecting.active').wait_for()
        assert page.locator('#stopLoading').is_visible()
        delaying=False;reply(held.pop(),{'blocks':{}})
        page.evaluate('()=>window.delayedSelection')
        assert page.locator('#stopLoading').is_visible()
        assert page.locator('#startLoading').is_hidden()
        page.locator('#stopLoading').click()
        page.locator('#loadingTitle').filter(has_text='Loading stopped').wait_for()
        browser.close()
    print('PASS: delayed selection cannot overwrite active build UI; Stop still cancels the isolated mocked build')
finally:h.stop()

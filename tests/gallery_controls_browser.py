"""Changing controls in Community invalidates the cached Daily projection before return."""
import test_safety
import json
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as probe:
    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start();h.seed_personal();h.endpoint.request('/api/community/recommend',{'artwork':1001})
    before=h.snapshot()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900},extra_http_headers=h.headers())
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def reply(route,value):route.fulfill(content_type='application/json',body=json.dumps(value))
        page.route('**/api/auth-status',lambda route:reply(route,{'connected':True,'id':42,'username':'MockAccount'}))
        page.route('**/api/discovery/summary',lambda route:reply(route,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
        page.route('**/api/creator-metadata?*',lambda route:reply(route,{'creators':{}}))
        page.route('**/api/reaction-status?*',lambda route:reply(route,{'images':{}}))
        page.goto(h.base,wait_until='networkidle');page.locator('#tabGallery').click()
        page.evaluate('day=>window.selectGalleryDate(day,"all")',h.day)
        page.locator('#gallery .creator-card').first.wait_for()
        assert page.locator('#gallery .creator-card').count()==12
        page.locator('#tabCommunity').click();page.locator('#communityGrid .creator-card').wait_for()
        page.locator('#filterToggle').click();page.locator('#contentMenu [data-level="1"]').click()
        page.keyboard.press('Escape')
        page.locator('#communityMessage').filter(has_text='No recommendations match').wait_for()
        assert page.locator('#gallery .creator-card').count()==0,'hidden Daily cards must not retain filtered previews'
        page.locator('#tabGallery').click()
        page.locator('#loadSentinel').filter(has_text='No creators match').wait_for()
        assert page.locator('#gallery .creator-card').count()==0
        assert not errors,errors
        browser.close()
    after=h.snapshot()
    assert after['recommendations']==before['recommendations'] and after['audit']==before['audit']
    print('PASS: real isolated settings/filter endpoints invalidate both gallery projections; ledger/audit preserved')
finally:h.stop()

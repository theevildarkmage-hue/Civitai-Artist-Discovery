"""Actual desktop/mobile Community states, synthetic routed policy responses."""
import test_safety,json,socket
from playwright.sync_api import sync_playwright
from community_harness import Harness
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        for width in (1440,390):
            page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers());state={'code':'INVITE_ONLY'};errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
            page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Fixture'}))
            page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
            page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True,'available':True,'enabled':True,'optedIn':True}))
            page.route('**/api/community/status*',lambda r:reply(r,{'code':state['code'],'error':'Synthetic denial'},403 if state['code']!='UNAVAILABLE' else 503))
            page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
            for code,text in [('INVITE_ONLY','Community Beta is Invite Only'),('INVITE_REVOKED','revoked'),('INVITE_EXPIRED','expired'),('SUBMITTER_BLOCKED','blocked'),('BETA_DISABLED','disabled or under maintenance'),('UNAVAILABLE','temporarily unavailable')]:
                state['code']=code;page.locator('#communityRefresh').click()
                page.wait_for_function('(text)=>document.querySelector("#communityMessage").textContent.includes(text)',arg=text)
                assert page.locator('#communityRecommend').is_disabled()
                assert not page.locator('#tabCommunity').is_hidden()
                if code=='INVITE_ONLY':page.screenshot(path=str(test_safety.REPORTS/f'invite-only-{width}.png'))
            assert not errors,errors
            page.locator('#tabGallery').click();assert page.locator('#tabGallery').get_attribute('aria-pressed')=='true'
            assert page.title()=='Civitai Discovery 3.0 Beta'
            page.screenshot(path=str(test_safety.REPORTS/f'beta-branding-{width}.png'));page.close()
        browser.close()
    print('PASS: desktop/mobile invite-only, expired/revoked, blocked, maintenance/outage distinction, visible tab, Daily navigation and beta branding')
finally:h.stop()

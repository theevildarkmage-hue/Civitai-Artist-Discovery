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
            page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers());state={'code':'FORBIDDEN','connector':False};errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
            page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Fixture'}))
            page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
            page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True,'available':True,'enabled':state['connector'],'optedIn':state['connector']}))
            page.route('**/api/community/status*',lambda r:reply(r,{'code':state['code'],'error':'Synthetic denial'},403 if state['code']!='UNAVAILABLE' else 503))
            page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
            page.wait_for_function('()=>document.querySelector("#communityMessage").textContent.includes("invite-only at this time")')
            page.wait_for_function('()=>document.querySelector("#communitySentinel").getAttribute("aria-busy")==="false"')
            assert page.locator('#communitySentinel').is_hidden()
            assert page.locator('#communityRecommend').is_disabled()
            assert "isn't on the invitation list" not in page.locator('#communityMessage').inner_text()
            assert 'Synthetic denial' not in page.locator('#communityMessage').inner_text()
            page.screenshot(path=str(test_safety.REPORTS/f'connector-off-invite-only-{width}.png'))
            state.update(connector=True,code='INVITE_ONLY')
            page.reload(wait_until='networkidle');page.locator('#tabCommunity').click()
            for code,text in [('INVITE_ONLY','Community Beta is Invite Only'),('INVITE_REVOKED','revoked'),('INVITE_EXPIRED','expired'),('SUBMITTER_BLOCKED','blocked'),('BETA_DISABLED','disabled or under maintenance'),('UNAVAILABLE','temporarily unavailable')]:
                state['code']=code;page.locator('#communityRefresh').click()
                page.wait_for_function('(text)=>document.querySelector("#communityMessage").textContent.includes(text)',arg=text)
                assert page.locator('#communityRecommend').is_disabled()
                assert not page.locator('#tabCommunity').is_hidden()
                page.wait_for_function('()=>document.querySelector("#communitySentinel").getAttribute("aria-busy")==="false"')
                if code=='UNAVAILABLE':
                    assert page.locator('#communitySentinel').is_visible()
                    assert page.get_by_role('button',name='Retry loading artists',exact=True).is_visible()
                else:
                    assert page.locator('#communitySentinel').is_hidden()
                if code=='INVITE_ONLY':page.screenshot(path=str(test_safety.REPORTS/f'invite-only-{width}.png'))
            # A hosted FORBIDDEN is not evidence that this account lacks an invite.
            state['code']='FORBIDDEN';page.locator('#communityRefresh').click()
            page.wait_for_function('()=>document.querySelector("#communityMessage").textContent==="Synthetic denial"')
            assert page.locator('#communityRecommend').is_disabled()
            assert not errors,errors
            page.locator('#tabGallery').click();assert page.locator('#tabGallery').get_attribute('aria-pressed')=='true'
            assert page.title()=='Civitai Discovery 3.0 Beta'
            page.screenshot(path=str(test_safety.REPORTS/f'beta-branding-{width}.png'));page.close()
        browser.close()
    print('PASS: 16 desktop/mobile access cases: connector-off invite-only context, hosted denial distinctions, terminal access states without pagination retries, transient retry retained, Daily navigation and beta branding; synthetic responses')
finally:h.stop()

"""Desktop/mobile automatic connection and server-only invitation decisions; synthetic HTTP."""
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
            page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
            state={'code':'INVITE_ONLY'};errors=[];requests={'status':0,'configWrites':0,'recommendations':0}
            page.on('pageerror',lambda error:errors.append(str(error)))
            def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
            page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Fixture'}))
            page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
            def config(route):
                if route.request.method!='GET':requests['configWrites']+=1
                reply(route,{'production':True,'serverPolicy':True,'available':True,'enabled':True})
            page.route('**/api/community/beta',config)
            def status(route):
                requests['status']+=1
                if state['code'] in ('INVITED','OWNER'):
                    quota={'used':8,'remaining':None,'limit':None,'unlimited':True} if state['code']=='OWNER' else {'used':0,'remaining':3,'limit':3}
                    return reply(route,{'member':{'id':11107642 if state['code']=='OWNER' else 42,'username':'Fixture'},'quota':quota,'beta':{'available':True,'submissions':True}})
                reply(route,{'code':state['code'],'error':'Synthetic denial'},503 if state['code']=='UNAVAILABLE' else 403)
            page.route('**/api/community/status*',status)
            page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[],'nextCursor':None}))
            def recommend(route):requests['recommendations']+=1;reply(route,{'error':'Unexpected mutation'},500)
            page.route('**/api/community/recommend',recommend)
            page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
            for code,text in [('INVITE_ONLY','Community Beta is Invite Only'),('INVITE_REVOKED','revoked'),('INVITE_EXPIRED','expired'),('SUBMITTER_BLOCKED','blocked'),('BETA_DISABLED','disabled or under maintenance'),('UNAVAILABLE','temporarily unavailable'),('FORBIDDEN','Synthetic denial')]:
                state['code']=code;page.locator('#communityRefresh').click()
                page.wait_for_function('(text)=>document.querySelector("#communityMessage").textContent.includes(text)',arg=text)
                assert page.locator('#communityRecommend').is_disabled()
                assert not page.locator('#tabCommunity').is_hidden()
                page.wait_for_function('()=>document.querySelector("#communitySentinel").getAttribute("aria-busy")==="false"')
                if code=='UNAVAILABLE':
                    assert page.locator('#communitySentinel').is_visible()
                    assert page.get_by_role('button',name='Retry loading artists',exact=True).is_visible()
                else:assert page.locator('#communitySentinel').is_hidden()
            # A new server invitation takes effect on refresh, without a local toggle.
            state['code']='INVITED';page.locator('#communityRefresh').click()
            page.wait_for_function('()=>!document.querySelector("#communityRecommend").disabled')
            page.locator('#communityRecommend').click();assert page.locator('.community-dialog[open]').count()==1
            page.locator('#communityCancel').click()
            state['code']='OWNER';page.locator('#communityRefresh').click()
            page.wait_for_function('()=>document.querySelector("#communityQuota").textContent.includes("Unlimited")')
            page.locator('#communityRecommend').click()
            assert '8 recommendations' in page.locator('#communityFormQuota').inner_text()
            assert 'three-per-artist' in page.locator('#communityFormQuota').inner_text()
            assert 'null' not in page.locator('#communityFormQuota').inner_text()
            page.locator('#communityCancel').click()
            state['code']='INVITED';page.locator('#communityRefresh').click()
            page.wait_for_function('()=>document.querySelector("#communityQuota").textContent.includes("3 left")')
            page.screenshot(path=str(test_safety.REPORTS/f'automatic-invited-{width}.png'))
            # Fresh reload also connects automatically and has no opt-in UI or writes.
            page.reload(wait_until='networkidle');page.locator('#tabCommunity').click()
            page.wait_for_function('()=>!document.querySelector("#communityRecommend").disabled')
            page.locator('#tabDiscovery').click()
            assert page.get_by_text('Opt in to Community Beta',exact=False).count()==0
            assert page.get_by_text('Beta privacy and authenticated activity',exact=False).count()==0
            assert requests['status']>0 and requests['configWrites']==0 and requests['recommendations']==0
            assert not errors,errors
            page.locator('#tabGallery').click();assert page.locator('#tabGallery').get_attribute('aria-pressed')=='true'
            assert page.title()=='Civitai Discovery 3.0 Beta';page.close()
        browser.close()
    print('PASS: desktop/mobile automatic invited access on fresh load/reload and post-invite refresh; seven server-denial states, no opt-in UI/config writes, no recommendation writes; synthetic HTTP')
finally:h.stop()

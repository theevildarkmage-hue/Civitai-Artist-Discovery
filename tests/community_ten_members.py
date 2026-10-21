"""Thirty UI confirmations in a new, challenged fixture sandbox; no real credentials."""
import test_safety
from collections import Counter
import json
import socket
import sys
from playwright.sync_api import sync_playwright
from community_harness import Harness
sys.path.insert(0,str(test_safety.ROOT))


with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start()
    from discovery.community import FixtureVerifier
    verifier=FixtureVerifier(development=True);plan=[];artists=Counter()
    for n in range(30):
        rating=(1,2,4,8,16)[n%5]
        art=next(art for art in verifier.artworks.values() if art.id not in plan and art.id!=1099
            and art.rating==rating and artists[art.creator_id]<3)
        plan.append(art.id);artists[art.creator_id]+=1
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},extra_http_headers=h.headers())
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/auth-status',lambda route:route.fulfill(content_type='application/json',
            body=json.dumps({'connected':True,'id':42,'username':'MockAccount'})))
        page.route('**/api/discovery/summary',lambda route:route.fulfill(content_type='application/json',
            body=json.dumps({'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'})))
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        assert page.locator('#communityDemo').count()==0
        for member in range(1,11):
            page.locator('#tabDiscovery').click()
            if not page.locator('#communityDiagnostics').evaluate('(e)=>e.open'):page.locator('#communityDiagnostics summary').click()
            with page.expect_response(lambda response:'/api/community/development/member' in response.url):
                page.locator('#communityMember').select_option(str(member))
            page.locator('#tabCommunity').click()
            page.locator('#communityQuota').filter(has_text='3 left').wait_for()
            for index,image in enumerate(plan[(member-1)*3:member*3]):
                page.locator('#communityRecommend').click()
                page.locator('#communityLink').fill(f'https://civitai.com/images/{image}')
                assert len(h.snapshot()['recommendations'])==(member-1)*3+index
                page.locator('#communitySubmit').click();page.locator('#communityDialogTitle').wait_for(state='hidden')
                assert len(h.snapshot()['recommendations'])==(member-1)*3+index+1
            page.locator('#communityQuota').filter(has_text='0 left').wait_for()
        page.locator('#communityRefresh').click();page.wait_for_function("()=>document.querySelectorAll('#communityGrid .image-progress button').length>=24")
        assert page.locator('#communityMore').count()==0
        page.locator('#communitySentinel').scroll_into_view_if_needed();page.wait_for_function("()=>document.querySelectorAll('#communityGrid .image-progress button').length===30")
        names=page.locator('#communityGrid .creator-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.username)')
        assert len(names)==len(set(names))<30
        baseline=h.snapshot();assert len(baseline['recommendations'])==len(baseline['audit'])==30
        h.stop();h.start();page.reload(wait_until='networkidle');page.locator('#tabCommunity').click()
        page.locator('#communityQuota').filter(has_text='0 left').wait_for()
        assert page.locator('#communityMember').input_value()=='10'
        assert h.snapshot()==baseline
        h.control('offline',enabled=True);page.locator('#communityRefresh').click()
        page.locator('#communityGrid .creator-card').first.wait_for()
        assert h.snapshot()==baseline
        assert not errors,errors
        browser.close()
    print('PASS: ten simulated members, 30 single-submit modal recommendations, pagination, quotas, restart identity/history, cached outage browsing')
finally:h.stop()

"""Synthetic isolated regression for remote policy in a development UI."""
import test_safety
import json,socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start();state={'submissions':False,'stale':False,'missing':False,'failure':False};writes=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(extra_http_headers=h.headers())
        def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
        page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Fixture'}))
        page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
        page.route('**/api/community/beta',lambda r:reply(r,{'production':False,'serverPolicy':True,'enabled':True}))
        def status(route):
            if state['failure']:return reply(route,{'error':'Synthetic status unavailable'},503)
            data=h.endpoint.request('/api/community/status')
            if not state['missing']:data['beta']={'available':True,'submissions':state['submissions']}
            if state['stale']:data['cache']={'state':'stale'}
            reply(route,data)
        page.route('**/api/community/status*',status)
        def mutation(route):writes.append(route.request.url);reply(route,{'error':'Unexpected mutation'},500)
        page.route('**/api/community/recommend',mutation)
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        page.wait_for_function("()=>document.querySelector('#communityQuota').textContent.includes('3 left')")
        def denied():
            assert page.locator('#communityRecommend').is_disabled()
            assert page.locator('#communitySubmit').is_disabled()
            page.evaluate("()=>{window.CivitaiCommunity.recommend('1001');document.querySelector('#communityLink').value='1001';document.querySelector('#communityForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));}")
            page.locator('.community-dialog[open]').wait_for(state='hidden')
            assert writes==[]
        denied()
        state['submissions']=True;page.locator('#communityRefresh').click()
        page.wait_for_function("()=>!document.querySelector('#communityRecommend').disabled")
        page.locator('#communityRecommend').click();page.locator('.community-dialog[open]').wait_for();assert page.locator('.community-dialog[open]').count()==1
        state['submissions']=False;page.evaluate('()=>window.CivitaiCommunity.load(false,true)')
        page.wait_for_function("()=>document.querySelector('#communityRecommend').disabled");denied()
        for flags in ({'submissions':True,'stale':True},{'stale':False,'missing':True},{'missing':False,'failure':True}):
            state.update(flags);page.locator('#communityRefresh').click()
            page.wait_for_timeout(200);denied()
        browser.close()
    print('PASS: development remote read-only policy, direct dialog/form guards, live disable, stale/missing/failed policy fail closed; no mutation requests; synthetic fixtures only')
finally:h.stop()

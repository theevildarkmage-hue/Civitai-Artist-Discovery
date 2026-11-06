"""Paused reporting and safe recommender links; synthetic isolated transport."""
import test_safety
import json
import socket
from urllib.parse import quote
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start();h.endpoint.request('/api/community/recommend',{'artwork':1001})
    h.endpoint.request('/api/community/recommend',{'artwork':1002},member=2)
    baseline=h.snapshot()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(extra_http_headers=h.headers())
        context.route('https://civitai.red/user/**',lambda r:r.fulfill(content_type='text/html',body='<title>Synthetic profile</title>'))
        page=context.new_page()
        state={'malicious':False};writes=[];errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def reply(route,data,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
        page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Synthetic'}))
        page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
        page.route('**/api/community/beta',lambda r:reply(r,{'production':False,'serverPolicy':True,'enabled':True}))
        def status(route):
            data=h.endpoint.request('/api/community/status')
            data['beta']={'available':True,'submissions':False,'reporting':True}
            reply(route,data)
        page.route('**/api/community/status*',status)
        attack='<img src=x onerror="window.creditInjected=true">'
        def gallery(route):
            data=h.endpoint.request('/api/community/gallery')
            if state['malicious']:
                for row in data['items']:row['recommender']=attack
            reply(route,data)
        page.route('**/api/community/gallery?*',gallery)
        def report(route):writes.append(route.request.post_data_json);reply(route,{'reported':True})
        page.route('**/api/community/report',report)
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':844})
            page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
            card=page.locator('#communityGrid .community-card');card.wait_for()
            assert card.count()==1 and page.locator('.community-report').count()==0
            assert page.get_by_role('button',name='Report recommendation',exact=True).count()==0
            assert page.locator('#communityRecommend').is_disabled()
            link=card.locator('.community-credit a')
            assert link.inner_text()=='DemoMember02'
            assert link.get_attribute('href')=='https://civitai.red/user/DemoMember02'
            assert link.get_attribute('target')=='_blank'
            assert {'noopener','noreferrer'}<=set(link.get_attribute('rel').split())
            with page.expect_popup() as opened:link.click()
            popup=opened.value;popup.wait_for_load_state()
            assert popup.url=='https://civitai.red/user/DemoMember02';popup.close()
            card.locator('.next').click()
            page.wait_for_function('()=>document.querySelector(".community-credit a").textContent==="DemoMember01"')
            assert link.get_attribute('href')=='https://civitai.red/user/DemoMember01'
            page.locator('#communityReportForm').evaluate('(e)=>e.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))')
            assert writes==[] and not page.locator('#communityReportForm').evaluate('(e)=>e.closest("dialog").open')
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.screenshot(path=str(test_safety.REPORTS/f'recommender-link-{width}.png'))
        state['malicious']=True;page.locator('#communityRefresh').click()
        page.wait_for_function('(text)=>document.querySelector(".community-credit a")?.textContent===text',arg=attack)
        assert page.locator('.community-credit a').get_attribute('href')=='https://civitai.red/user/'+quote(attack,safe="~()*!.'-")
        assert page.locator('.community-credit img').count()==0 and not page.evaluate('window.creditInjected===true')
        assert writes==[] and h.snapshot()==baseline and not errors
        browser.close()
    print('PASS: desktop/mobile removed report entry, guarded form, new-tab profile links, carousel attribution, encoded hostile names and preserved ledger; synthetic transport')
finally:h.stop()

"""Report UI lifecycle; isolated fixture server and mocked report transport only."""
import test_safety
import json
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start();h.endpoint.request('/api/community/recommend',{'artwork':1001})
    baseline=h.snapshot()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(extra_http_headers=h.headers())
        state={'reporting':True};held=[];writes=[];errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def reply(route,data,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
        page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Synthetic'}))
        page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
        page.route('**/api/community/beta',lambda r:reply(r,{'production':False,'serverPolicy':True,'enabled':True}))
        def status(route):
            data=h.endpoint.request('/api/community/status')
            data['beta']={'available':state['reporting'],'submissions':False,'reporting':state['reporting']}
            reply(route,data)
        page.route('**/api/community/status*',status)
        def report(route):writes.append(route.request.post_data_json);held.append(route)
        page.route('**/api/community/report',report)
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        button=page.locator('.community-report');button.wait_for()
        assert page.locator('#communityRecommend').is_disabled()
        for width in (1440,390):
            page.set_viewport_size({'width':width,'height':844})
            button.click();page.locator('#communityReportReason').select_option('ownership')
            page.locator('#communityReportSubmit').click()
            while not held:page.wait_for_timeout(20)
            assert page.locator('#communityReportSubmit').is_disabled()
            page.locator('#communityReportForm').evaluate('(e)=>e.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))')
            assert writes[-1]=={'artwork':'1001','reason':'ownership'} and len(writes)==(1 if width==1440 else 2)
            reply(held.pop(),{'apiVersion':1,'reported':True})
            page.locator('#communityReportMessage').filter(has_text='Report received').wait_for()
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.screenshot(path=str(test_safety.REPORTS/f'report-{width}.png'))
            page.locator('#communityReportClose').click()
        button.click();state['reporting']=False
        page.evaluate('()=>window.CivitaiCommunity.load(false,true)')
        page.wait_for_function('()=>!document.querySelector("#communityReportForm").closest("dialog").open')
        assert not button.is_visible()
        page.locator('#communityReportForm').evaluate('(e)=>e.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))')
        assert len(writes)==2 and h.snapshot()==baseline and not errors
        browser.close()
    print('PASS: desktop/mobile report dialog, fixed reasons, identity-free payload, single flight, disabled policy, preserved fixture ledger; mocked report transport')
finally:h.stop()

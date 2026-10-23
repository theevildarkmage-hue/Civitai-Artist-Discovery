"""Real browser coordinator journeys; synthetic paged transport, disposable server only."""
import test_safety
import json
import os
import socket
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as probe:
    probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
h=Harness(port=port,sample=False)

def run(width):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
        calls=[];held=[];slow_filtered=[];errors=[];hold_second=True;fail_third=True;filtered=False
        page.on('pageerror',lambda error:errors.append(str(error)))
        def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
        page.route('**/api/auth-status',lambda route:reply(route,{'connected':True,'id':42,'username':'MockAccount'}))
        page.route('**/api/discovery/summary',lambda route:reply(route,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
        page.route('**/api/creator-metadata?*',lambda route:reply(route,{'creators':{}}))
        page.route('**/api/reaction-status?*',lambda route:reply(route,{'images':{}}))
        def response(number):
            items=[]
            # Filtered pages can be empty while still carrying an opaque next cursor.
            # A repeated boundary row must not produce another card or lose a cursor.
            start=(number-1)*12-(1 if number>1 and not filtered else 0)
            for index in ([] if filtered and number<4 else range(start,number*12)):
                art={'id':1001+index,'creatorId':2000+index,'username':f'SyntheticArtist{index}',
                    'profileUrl':f'https://civitai.red/user/SyntheticArtist{index}',
                    'imageUrl':f'https://civitai.red/images/{1001+index}',
                    'url':f'/community-fixture.svg?id={1001+index}',
                    'thumbnailUrl':f'/community-fixture.svg?id={1001+index}',
                    'type':'image','tagState':{'known':True,'tags':[]}}
                items.append({'artwork':art,'recommender':'SyntheticMember','submittedAt':'2026-10-21T12:00:00Z'})
            return {'items':items,'nextCursor':str(number+1) if number<4 else None}
        def gallery(route):
            nonlocal fail_third
            number=int(parse_qs(urlsplit(route.request.url).query).get('cursor',['1'])[0])
            calls.append((filtered,number))
            if filtered and number==2 and os.environ.get("CAD_TEST_SLOW_FILTER"):
                slow_filtered.append(route);return
            if number==2 and hold_second and not filtered:held.append(route);return
            if number==3 and fail_third and not filtered:
                fail_third=False;reply(route,{'error':'Synthetic offline page'},503);return
            reply(route,response(number))
        page.route('**/api/community/gallery?*',gallery)
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .creator-card').length===12")
        assert calls==[(False,1)],calls
        assert page.locator('#communityMore').count()==0
        sentinel=page.locator('#communitySentinel')
        sentinel.scroll_into_view_if_needed();page.mouse.wheel(0,3000)
        for _ in range(50):
            if held:break
            page.wait_for_timeout(20)
        assert len(held)==1
        original_aborted_route=held[0]
        assert sentinel.get_attribute('aria-busy')=='true'
        assert 'Loading' in sentinel.inner_text()
        for _ in range(5):page.mouse.wheel(0,3000)
        page.wait_for_timeout(100);assert calls.count((False,2))==1
        saved=page.evaluate('scrollY')
        page.locator('#tabGallery').click();page.wait_for_timeout(100)
        page.locator('#tabCommunity').click()
        assert page.locator('#communityGrid .creator-card').count()==12
        assert calls.count((False,1))==1,'returning to a tab must retain loaded pages'
        assert abs(page.evaluate('scrollY')-saved)<80
        # Force a resumed request to coexist with the old aborted route. The
        # previous fixture indiscriminately aborted both after returning to the
        # tab, intermittently converting a valid retry into a simulated outage.
        page.mouse.wheel(0,500);sentinel.scroll_into_view_if_needed()
        for _ in range(100):
            if len(held)>1:break
            page.wait_for_timeout(20)
        assert len(held)==2,'resume must retry the aborted cursor once'
        hold_second=False
        for route in held:
            try:
                if route is original_aborted_route:route.abort()
                else:reply(route,response(2))
            except Exception:pass
        held.clear()
        # Resume retries only the aborted cursor, never the first page.
        page.mouse.wheel(0,500);sentinel.scroll_into_view_if_needed()
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .creator-card').length===24")
        ids=page.locator('#communityGrid .creator-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.id)')
        assert len(ids)==len(set(ids)), 'overlapping cursor pages must deduplicate'
        before=page.evaluate('scrollY')
        assert before>=saved-80
        sentinel.scroll_into_view_if_needed();page.mouse.wheel(0,3000)
        page.get_by_role('button',name='Retry loading artists',exact=True).wait_for()
        failed_count=calls.count((False,3))
        for _ in range(3):page.mouse.wheel(0,3000)
        page.wait_for_timeout(100);assert calls.count((False,3))==failed_count
        page.get_by_role('button',name='Retry loading artists',exact=True).click()
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .creator-card').length===36")
        # Begin another page, then change shared controls while it is in flight.
        fourth=[]
        def hold_fourth(route):fourth.append(route)
        page.route('**/api/community/gallery?*cursor=4',hold_fourth)
        sentinel.scroll_into_view_if_needed();page.mouse.wheel(0,3000)
        for _ in range(50):
            if fourth:break
            page.wait_for_timeout(20)
        assert fourth
        filtered=True;page.unroute('**/api/community/gallery?*cursor=4',hold_fourth)
        # Use the actual shared settings endpoint, solely in this challenged fixture.
        page.locator('#contentFilter').click();page.locator('#contentPanel [data-level="16"]').click()
        page.keyboard.press('Escape')
        for route in fourth:
            try:route.fulfill(content_type='application/json',body=json.dumps(response(4)))
            except Exception:pass
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .creator-card').length===0")
        page.wait_for_timeout(500)
        empty_calls=[n for f,n in calls if f]
        assert len(empty_calls)<=3,'empty filtered pages must not drain an entire feed automatically'
        page.mouse.wheel(0,600)
        if slow_filtered:
            page.wait_for_timeout(100)
            for route in slow_filtered:reply(route,response(2))
            slow_filtered.clear()
        try:
            # Intent while an empty page is pending can coalesce into its next
            # page budget. Wait for completion, then supply further user intent;
            # a fixed delay cannot establish that all initial pages have settled.
            for _ in range(3):
                page.wait_for_function("()=>document.querySelector('#communitySentinel').getAttribute('aria-busy')==='false'")
                if page.locator('#communityGrid .creator-card').count()==12:break
                page.mouse.wheel(0,600)
                page.wait_for_timeout(100)
            page.wait_for_function("()=>document.querySelectorAll('#communityGrid .creator-card').length===12",timeout=10000)
            assert [n for f,n in calls if f]==[1,2,3,4], 'filtered cursor progression must be ordered and single-flight'
        except Exception:
            state=page.evaluate("()=>({cards:document.querySelectorAll('#communityGrid .creator-card').length,scrollY,dialogs:[...document.querySelectorAll('dialog[open]')].map(x=>x.id),sentinel:document.querySelector('#communitySentinel').outerHTML,bounds:document.querySelector('#communitySentinel').getBoundingClientRect().toJSON()})")
            (test_safety.REPORTS/f'filtered-timeout-{width}.json').write_text(json.dumps({'calls':calls,'emptyCallsBeforeWheel':empty_calls,'browser':state,'errors':errors},indent=2))
            page.screenshot(path=str(test_safety.REPORTS/f'filtered-timeout-{width}.png'))
            raise
        sentinel.scroll_into_view_if_needed();page.mouse.wheel(0,1000)
        sentinel.filter(has_text="caught up").wait_for()
        assert sentinel.get_attribute('aria-busy')=='false'
        final_calls=len(calls)
        for _ in range(3):page.mouse.wheel(0,3000)
        page.wait_for_timeout(100);assert len(calls)==final_calls
        ids=page.locator('#communityGrid .creator-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.id)')
        assert len(ids)==len(set(ids)) and min(map(int,ids))>=1037
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        page.screenshot(path=str(test_safety.REPORTS/f'pagination-{width}.png'))
        assert not errors,errors
        browser.close()

try:
    h.start();run(1440);run(390)
    print('PASS: desktop/mobile automatic pages, single flight, aborted navigation, restored scroll, failure retry, filtered empty pages, stale response rejection, exhaustion')
finally:h.stop()

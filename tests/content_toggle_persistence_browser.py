"""Real disposable settings API: stale replies, failed saves, lost replies and cross-tab sync."""
import test_safety
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start();h.endpoint.request('/api/settings',{'browsingLevels':[1,2]})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(extra_http_headers=h.headers())
        pages=[context.new_page(),context.new_page()]
        writes=[];held=[];mode=['normal'];errors=[]
        for page in pages:
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/api/auth-status',lambda r:r.fulfill(json={'connected':True,'id':42,'username':'SyntheticControls','socialWrite':True}))
            page.route('**/api/discovery/summary',lambda r:r.fulfill(json={'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
            def settings(route):
                if route.request.method!='POST':route.continue_();return
                body=route.request.post_data_json;writes.append(body)
                if mode[0]=='reject':route.fulfill(status=400,json={'error':'Synthetic rejected save'});return
                result=h.endpoint.request('/api/settings',body)
                if mode[0]=='hold-rating' and 'browsingLevels' in body:held.append((route,result));return
                if mode[0]=='lost':route.abort();return
                route.fulfill(json=result)
            page.route('**/api/settings',settings)
            page.goto(h.base,wait_until='networkidle')
            page.wait_for_function('() => oauthConnected')
        page,peer=pages
        mode[0]='hold-rating'
        page.evaluate('void chooseContentRating([1,2,4])')
        for _ in range(100):
            if held:break
            page.wait_for_timeout(10)
        assert len(held)==1
        page.evaluate('setGalleryTagFilters(false,false)')
        assert not page.evaluate('galleryFilterState().applyTagFilters')
        for route,result in held:route.fulfill(json=result)
        page.wait_for_function('() => !contentSelection.pending')
        page.wait_for_timeout(100)
        assert not h.endpoint.request('/api/settings')['applyTagFilters']
        assert not page.evaluate('galleryFilterState().applyTagFilters'),'Old rating response restored an obsolete toggle value'
        peer.wait_for_function('() => !galleryFilterState().applyTagFilters')

        mode[0]='reject';before=len(writes)
        page.evaluate('setGalleryTagFilters(true,false)')
        assert len(writes)==before+1
        assert not page.evaluate('galleryFilterState().applyTagFilters')
        assert not h.endpoint.request('/api/settings')['applyTagFilters']
        mode[0]='lost';before=len(writes)
        page.evaluate('setGalleryAccountFilters(false,false)')
        assert len(writes)==before+1,'Lost response must read back without replaying the write'
        assert not page.evaluate('galleryFilterState().applyAccountFilters')
        peer.wait_for_function('() => !galleryFilterState().applyAccountFilters')
        assert not h.endpoint.request('/api/settings')['applyAccountFilters']
        assert not errors,errors
        browser.close()
    print('PASS: authoritative settings, stale cross-category response, failed-save rollback, lost-response readback without replay and real BroadcastChannel synchronization; synthetic account only')
finally:h.stop()

"""Shared card interaction contract in a disposable server with mocked social transport."""
import test_safety
import json
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as probe:
    probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
h=Harness(port=port,sample=False)
try:
    h.start()
    for image in (1001,1002,1004): h.endpoint.request('/api/community/recommend',{'artwork':image})
    before=h.snapshot()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900},extra_http_headers=h.headers())
        errors=[];writes=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def reply(route, value): route.fulfill(content_type='application/json',body=json.dumps(value))
        page.route('**/api/auth-status',lambda route:reply(route,{'connected':True,'id':42,
            'username':'MockAccount','socialWrite':True,'userWrite':True,'collectionsWrite':True}))
        page.route('**/api/discovery/summary',lambda route:reply(route,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
        page.route('**/api/creator-metadata?*',lambda route:reply(route,{'creators':{
            'demoartist1':{'username':'DemoArtist1','userId':101,'following':False,'followers':27}}}))
        page.route('**/api/reaction-status?*',lambda route:reply(route,{'images':{
            str(i):{'reactions':[],'stats':{'likeCount':5,'reactionCount':5}} for i in (1001,1002,1004)}}))
        def mutation(route):
            value=route.request.post_data_json; writes.append((route.request.url.split('/')[-1],value))
            if route.request.url.endswith('/follow'): reply(route,{'userId':101,'following':True,'changed':True})
            elif route.request.url.endswith('/reaction'): reply(route,{'reactions':['Like'],'stats':{'likeCount':6,'reactionCount':6}})
            else: reply(route,{'hidden':True})
        page.route('**/api/follow',mutation);page.route('**/api/reaction',mutation)
        page.route('**/api/content-controls/hide-artist',mutation)
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        card=page.locator('#communityGrid .creator-card');card.wait_for()
        assert card.count()==1
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .image-progress button').length===3")
        card.locator('.reaction-bar [data-reaction="Like"] b').filter(has_text='5').wait_for()
        card.locator('.follow-button').click();card.locator('.follow-button').filter(has_text='Following').wait_for()
        card.locator('[data-reaction="Like"]').click()
        card.locator('[data-reaction="Like"].selected b').filter(has_text='6').wait_for()
        assert writes[0][1]['userId']==101 and writes[1][1]['imageId']==1004
        card.locator('.next').click();page.wait_for_function("()=>document.querySelector('#communityGrid .creator-card').dataset.id==='1002'")
        assert 'Recommended by DemoMember01' in card.locator('.community-credit').inner_text()
        card.locator('.info-button').click();page.locator('#detailTags').filter(has_text='tag').wait_for()
        assert page.locator('dialog[open]').count()==1
        page.locator('#close').click()
        card.locator('.more-menu').click()
        assert page.get_by_role('menuitem',name='Add image to collection').is_visible()
        page.get_by_role('menuitem',name='Recommend to Community').click()
        assert page.locator('#communityLink').input_value()=='1002'
        page.keyboard.press('Escape')
        page.set_viewport_size({'width':390,'height':844})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        page.on('dialog',lambda dialog:dialog.accept())
        card.locator('.more-menu').click();page.get_by_role('menuitem',name='Hide this artist').click()
        card.wait_for(state='detached')
        assert writes[-1][1]=={'username':'DemoArtist1','userId':101}
        assert h.snapshot()==before
        assert not errors,errors
        browser.close()
    print('PASS: one artist carousel; shared follow/reaction/counts/detail/menu/hide/mobile; social writes mocked; ledger unchanged')
finally: h.stop()

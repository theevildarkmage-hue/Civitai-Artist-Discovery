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
        page.route('**/api/follow-status?*',lambda route:reply(route,{'accountId':42,'following':{'101':False}}))
        page.route('**/api/reaction-status?*',lambda route:reply(route,{'images':{
            str(i):{'reactions':[],'stats':{'likeCount':5,'reactionCount':5}} for i in (1001,1002,1004)}}))
        def mutation(route):
            value=route.request.post_data_json; writes.append((route.request.url.split('/')[-1],value))
            if route.request.url.endswith('/follow'): reply(route,{'userId':101,'following':value['following'],'changed':True})
            elif route.request.url.endswith('/reaction'): reply(route,{'reactions':['Like'],'stats':{'likeCount':6,'reactionCount':6}})
            elif route.request.url.endswith('/collections/add'): reply(route,{'collectionName':'Synthetic collection','added':True})
            else: reply(route,{'hidden':True})
        page.route('**/api/follow',mutation);page.route('**/api/reaction',mutation)
        page.route('**/api/content-controls/hide-artist',mutation)
        page.route('**/api/collections',lambda route:reply(route,{'collections':[{'id':77,'name':'Synthetic collection'}]}))
        page.route('**/api/collections/add',mutation)
        page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
        card=page.locator('#communityGrid .creator-card');card.wait_for()
        page.wait_for_function("()=>document.querySelector('#communityGrid .creator-card').getAttribute('aria-busy')==='false'")
        card.locator('.info-button').click()
        assert page.locator('#detailPrevious').inner_text()=='\u2190 Previous'
        assert page.locator('#detailNext').inner_text()=='Next \u2192'
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='1 of 3'")
        initial=card.get_attribute('data-id'); handle=card.element_handle()
        page.locator('#detailNext').click()
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='2 of 3' && !document.querySelector('#detailNext').disabled")
        assert card.get_attribute('data-id')!=initial
        assert page.locator('#detailAttribution').inner_text()==card.locator('.community-credit').inner_text()
        page.keyboard.press('ArrowLeft')
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='1 of 3' && !document.querySelector('#detailNext').disabled")
        page.keyboard.press('ArrowLeft')
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='3 of 3' && !document.querySelector('#detailNext').disabled")
        page.keyboard.press('Escape'); assert not page.locator('#details').evaluate('el=>el.open')
        assert handle.evaluate('el=>el.isConnected')
        card.locator('.info-button').click(); assert page.locator('#detailPosition').inner_text()=='3 of 3'
        # Native controls keep their own arrow semantics.
        page.evaluate("()=>{let input=document.createElement('input');input.id='native-detail';document.querySelector('.detail-copy').append(input);input.focus()}")
        page.keyboard.press('ArrowRight'); assert page.locator('#detailPosition').inner_text()=='3 of 3'
        page.keyboard.press('Escape')
        # The shared adapter permits a delayed real tag gate: closing cancels its
        # eventual selection, while a hidden candidate is skipped in either direction.
        page.evaluate("""()=>{
          const old=document.querySelector('#communityGrid .creator-card');
          const art={id:90001,username:'NavigationFixture',type:'image',url:location.origin+'/community-fixture.svg',thumbnailUrl:location.origin+'/community-fixture.svg',civitaiUrl:'https://civitai.com/images/90001'};
          window.gateMode='allow';
          const images=[art,{...art,id:90002},{...art,id:90003}];
          const test=window.DiscoveryGallery.createCard({username:art.username,imageCount:3,representative:art},{images,
            checkImageTags:async id=>{if(window.gateMode==='fail')throw new Error('Synthetic temporary tag failure');if(window.gateMode==='wait'&&id===90002)await new Promise(r=>window.releaseGate=r);return {known:true,hidden:window.gateMode==='hide'&&id===90002}},
            tagsHideImage:r=>r.hidden,loadMore:async()=>{}});
          old.replaceWith(test);window.testCard=test;test.prepareArtwork();
        }""")
        page.route('**/api/history/image?id=900*',lambda route:reply(route,{'id':int(route.request.url.split('=')[-1]),'known':True,'tags':[],'url':h.base+'/community-fixture.svg','civitaiUrl':'https://civitai.com/images/90001'}))
        page.wait_for_function("()=>window.testCard.getAttribute('aria-busy')==='false'")
        card.locator('.info-button').click();page.evaluate("window.gateMode='wait'")
        page.locator('#detailNext').click();page.wait_for_function('()=>!!window.releaseGate')
        page.keyboard.press('Escape');page.evaluate('window.releaseGate()')
        page.wait_for_function("()=>window.testCard.getAttribute('aria-busy')==='false'")
        assert card.get_attribute('data-id')=='90001'
        card.locator('.info-button').click();page.evaluate("window.gateMode='hide'")
        page.locator('#detailNext').click()
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='2 of 2' && !document.querySelector('#detailNext').disabled")
        assert card.get_attribute('data-id')=='90003'
        page.set_viewport_size({'width':390,'height':844})
        assert page.locator('#detailNext').is_visible()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        page.screenshot(path=str(test_safety.REPORTS/'details-navigation-mobile.png'))
        page.evaluate("window.gateMode='fail'")
        page.locator('#detailNext').click()
        page.locator('#toast').filter(has_text='Synthetic temporary tag failure').wait_for()
        assert card.get_attribute('data-id')=='90003'
        assert not page.locator('#detailNext').is_disabled()
        page.keyboard.press('Escape')
        # Lazy representative at position two; the first move uses its real index.
        page.evaluate("""()=>{
          const old=window.testCard, art={...old.detailArtwork().image,id:90002};
          const test=window.DiscoveryGallery.createCard({username:'LazyFixture',imageCount:3,representativeIndex:1,representative:art},{
            api:async()=>({images:[{...art,id:90001},art,{...art,id:90003}]}),
            checkImageTags:async()=>({known:true,tags:[]})});
          old.replaceWith(test);window.testCard=test;test.prepareArtwork();
        }""")
        page.wait_for_function("()=>window.testCard.getAttribute('aria-busy')==='false'")
        card.locator('.info-button').click()
        assert page.locator('#detailPosition').inner_text()=='2 of 3'
        page.locator('#detailNext').click()
        page.wait_for_function("()=>document.querySelector('#detailPosition').textContent==='3 of 3'&&!document.querySelector('#detailNext').disabled")
        assert card.get_attribute('data-id')=='90003'
        page.keyboard.press('Escape')
        page.evaluate("()=>{let c=window.testCard;let art=c.detailArtwork().image;c.appendImages([]);window.single=window.DiscoveryGallery.createCard({username:'SingleFixture',imageCount:1,representative:art},{images:[art],checkImageTags:async()=>({known:true,tags:[]})});c.replaceWith(window.single);window.single.prepareArtwork()}")
        page.wait_for_function("()=>window.single.getAttribute('aria-busy')==='false'")
        card.locator('.info-button').click()
        assert page.locator('#detailNavigation').is_hidden()
        page.keyboard.press('ArrowRight');assert card.get_attribute('data-id')=='90003'
        assert not writes,writes
        assert not errors,errors
        assert h.snapshot()==before
        browser.close()
    print('PASS: details forward/back/wrap/reopen/attribution; native keys; cancelled gate; hidden skip; mobile; no social writes. Synthetic transport only.')
finally:h.stop()

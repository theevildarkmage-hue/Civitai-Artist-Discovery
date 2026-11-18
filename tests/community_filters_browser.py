"""Full Community Filters surface and functional loaded-page model/media selection."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json,threading
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture

server=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900});errors=[];calls=[];follow_writes=[];pending_profiles=[];defer_profiles=True
   page.on('pageerror',lambda error:errors.append(str(error)))
   install_fixture(page,base,cached_tags=True)
   def reply(route,value):route.fulfill(content_type='application/json',body=json.dumps(value))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True}))
   page.route('**/api/community/config',lambda r:reply(r,{'development':False}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':7},'quota':{'remaining':3},'beta':{'available':True,'submissions':True}}))
   def gallery(route):
    number=int(parse_qs(urlsplit(route.request.url).query).get('cursor',['1'])[0]);calls.append(number)
    rows=[]
    for index in (range(1,5) if number==1 else range(5,7)):
     model={1:'Illustrious',2:'Flux.1 D',3:'YuE2-3B',4:'Unknown',5:'Anima',6:'Flux.1 D'}[index]
     art={'id':index,'creatorId':100+index,'username':f'Synthetic{index}','browsingLevel':1,
      'type':'video' if index==3 else 'image','baseModel':model,'modelLabels':[model],
      'url':f'https://image.civitai.com/fixture/{index}.svg','thumbnailUrl':f'https://image.civitai.com/fixture/{index}.svg',
      'imageUrl':f'https://civitai.red/images/{index}','tagState':{'known':True,'tags':[]}}
     rows.append({'artwork':art,'submittedAt':'2026-10-24T12:00:00Z','recommender':'SyntheticMember'})
    reply(route,{'items':rows,'nextCursor':'2' if number==1 else None})
   page.route('**/api/community/gallery*',gallery)
   def follows(route):
    ids=parse_qs(urlsplit(route.request.url).query).get('userId',[])
    reply(route,{'accountId':7,'following':{value:int(value)%2==1 for value in ids}})
   page.route('**/api/follow-status?*',follows)
   def follow_write(route):
    value=route.request.post_data_json;follow_writes.append(value)
    reply(route,{'userId':value['userId'],'following':value['following'],'changed':True})
   page.route('**/api/follow',follow_write)
   def metadata(route):
    query=parse_qs(urlsplit(route.request.url).query)
    if query.get('follow')!=['0']:return route.fallback()  # Daily gallery cards
    # No following field: a profile reply must not clear the follow state above.
    result={'creators':{name.lower():{'username':name,'avatarUrl':f'https://image.civitai.com/fixture/{900+int(name[9:])}.svg'} for name in query.get('username',[])}}
    if defer_profiles:pending_profiles.append((route,result))
    else:reply(route,result)
   page.route('**/api/creator-metadata?*',metadata)
   page.goto(base,wait_until='domcontentloaded');page.locator('#tabCommunity').click()
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length>=4')
   page.locator('#communityGrid [data-username="synthetic1"] .follow-button').filter(has_text='Following').wait_for()
   page.locator('#communityGrid [data-username="synthetic2"] .follow-button').filter(has_text='+ Follow').wait_for()
   button=page.locator('#communityGrid [data-username="synthetic1"] .follow-button')
   button.click();button.filter(has_text='+ Follow').wait_for()
   assert len(follow_writes)==1 and follow_writes[0]['following'] is False
   # A delayed public profile reply must not undo the user's completed unfollow.
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length>=4')
   for route,result in pending_profiles:reply(route,result)
   defer_profiles=False
   page.wait_for_function('()=>document.querySelector(\'#communityGrid [data-username="synthetic1"] img.creator-avatar\')?.getAttribute("src")?.endsWith("/901.svg")')
   button.filter(has_text='+ Follow').wait_for()
   button.click();button.filter(has_text='Following').wait_for()
   assert len(follow_writes)==2 and follow_writes[1]['following'] is True
   page.locator('#filterToggle').click()
   page.locator('#mediaType').wait_for(state='visible')
   page.locator('.model-search').wait_for(state='visible')
   page.locator('#modelMenu input[value="Illustrious"]').wait_for(state='visible')
   assert page.locator('#contentMenu [data-level]').count()==5
   page.locator('#modelMenu input[value="Illustrious"]').check()
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length===1')
   assert page.locator('#communityGrid .creator-card').get_attribute('data-id')=='1'
   page.locator('#modelClear').click()
   page.locator('#mediaType').select_option('video')
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length===1')
   assert page.locator('#communityGrid .creator-card').get_attribute('data-id')=='3'
   page.locator('#modelMenu input[value="YuE2-3B"]').wait_for(state='visible')
   page.locator('#mediaType').select_option('all')
   page.locator('.model-search').fill('Illus')
   page.wait_for_function('()=>document.querySelectorAll("#modelMenu .filter-row:not(.search-hidden)").length===1')
   page.locator('.model-search').fill('')
   page.keyboard.press('Escape')
   page.locator('#communitySentinel').scroll_into_view_if_needed();page.mouse.wheel(0,1000)
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length===6')
   assert calls[-2:]==[1,2] and calls.count(2)<=calls.count(1),calls  # media changes recheck admission and start a new feed
   page.locator('#filterToggle').click()
   page.locator('#modelMenu input[value="Anima"]').wait_for(state='visible')
   page.screenshot(path=str(test_safety.REPORTS/f'full-community-filters-{width}.png'))
   page.keyboard.press('Escape');page.locator('#tabGallery').click()
   page.locator('#filterToggle').click()
   page.locator('#modelMenu input[value="SDXL"]').wait_for(state='visible')
   assert page.locator('#mediaType').is_visible() and page.locator('.model-search').is_visible()
   # Each imported control category has a persistent, independent viewing switch.
   page.get_by_label('Apply my tag filters',exact=True).uncheck()
   page.wait_for_function("()=>!window.galleryFilterState().controlFiltersSaving")
   assert not page.get_by_label('Apply my tag filters',exact=True).is_checked()
   assert page.get_by_label('Exclude hidden and blocked accounts',exact=True).is_checked()
   page.get_by_label('Exclude hidden and blocked accounts',exact=True).uncheck()
   page.wait_for_function("()=>!window.galleryFilterState().controlFiltersSaving")
   page.get_by_label('Exclude hidden artworks',exact=False).uncheck()
   page.wait_for_function("()=>!window.galleryFilterState().controlFiltersSaving")
   page.reload(wait_until='networkidle')
   page.locator('#filterToggle').click()
   assert not page.get_by_label('Apply my tag filters',exact=True).is_checked()
   assert not page.get_by_label('Exclude hidden and blocked accounts',exact=True).is_checked()
   assert not page.locator('#applyImageFilters').is_checked()
   assert page.locator('.filter-reset').is_enabled()
   page.locator('.filter-reset').click()
   page.wait_for_function("()=>window.galleryFilterState().applyTagFilters && window.galleryFilterState().applyAccountFilters && window.galleryFilterState().applyImageFilters && !window.galleryFilterState().controlFiltersSaving")
   assert page.get_by_label('Apply my tag filters',exact=True).is_checked()
   assert page.get_by_label('Exclude hidden and blocked accounts',exact=True).is_checked()
   page.route('**/api/follow-status?*',lambda route:route.fulfill(status=503,content_type='application/json',body='{}'))
   page.keyboard.press('Escape');page.locator('#tabCommunity').click();page.locator('#communityRefresh').click()
   unavailable=page.locator('#communityGrid [data-username="synthetic1"] .follow-button')
   unavailable.filter(has_text='Status unavailable').wait_for()
   assert unavailable.is_disabled()
   assert not errors,errors
   page.close()
  browser.close()
 print('PASS: desktop/mobile full panel, model selection/clear/search, media selection, next-page catalog, Daily restoration; synthetic HTTP/artwork, no submissions')
finally:server.shutdown();server.server_close()

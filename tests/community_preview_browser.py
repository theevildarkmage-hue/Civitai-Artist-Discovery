"""Real Chromium UI with synthetic HTTP/media: automatic preview stays read-only until explicit confirmation."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json,threading
from datetime import datetime,timedelta,timezone
from community_accepted_feed import make_client
from discovery.community import CommunityError
from playwright.sync_api import sync_playwright,Error
from v2_visual_baseline import ROOT,StaticHandler,install_fixture

http=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=http.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{http.server_port}'
svg='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><rect width="240" height="320" fill="#245361"/><circle cx="120" cy="112" r="42" fill="#e6b878"/><path d="M0 300L95 180L160 255L240 145V320H0" fill="#172e38"/></svg>'
results=[]
def reply(route,data,status=200):route.fulfill(json=data,status=status)
def artwork(i):return {'id':i,'creatorId':55,'username':'PreviewArtist','profileUrl':'https://civitai.red/user/PreviewArtist','imageUrl':f'https://civitai.red/images/{i}','type':'image','browsingLevel':1,'url':f'{base}/auto-preview.svg?id={i}','thumbnailUrl':f'{base}/auto-preview.svg?id={i}','tagState':{'known':True,'tags':[]},'stats':{}}
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390,320):
   page=browser.new_page(viewport={'width':width,'height':950});errors=[];calls=[];writes=[];held=[];media=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   install_fixture(page,base,cached_tags=True)
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True,'available':True}))
   page.route('**/api/community/config',lambda r:reply(r,{'development':False}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':7},'quota':{'used':0,'remaining':3,'limit':3},'beta':{'available':True,'submissions':True}}))
   client,backend,_=make_client(0)
   def gallery(route):
    try:reply(route,client.request('gallery',{}))
    except CommunityError as error:reply(route,error.response(),error.status)
   page.route('**/api/community/gallery*',gallery)
   page.route('**/api/history/images',lambda r:reply(r,{'images':[artwork(i) for i in r.request.post_data_json['imageIds']],'counts':{'contentControls':0,'unavailable':0}}))
   page.route('**/api/creator-metadata?*',lambda r:reply(r,{'creators':{'previewartist':{'userId':55,'following':False,'followers':12}}}))
   def verify(route):
    value=route.request.post_data_json['artwork'];calls.append(value);held.append(route)
   page.route('**/api/community/verify',verify)
   page.route('**/auto-preview.svg?*',lambda route:reply_media(route) if writes else media.append(route))
   def reply_media(route):route.fulfill(content_type='image/svg+xml',body=svg)
   def submit(route):
    writes.append(route.request.post_data_json)
    # The authoritative server clock can be ahead of the Windows client clock.
    backend.rows=[{'imageId':3105,'creatorId':55,'recommender':'Synthetic','submittedAt':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()}]
    reply(route,{'quota':{'used':1,'remaining':2}})
   page.route('**/api/community/recommend',submit)
   page.goto(base,wait_until='domcontentloaded');page.locator('#tabCommunity').click();page.locator('#communityRecommend').click()
   page.wait_for_function("()=>!document.querySelector('#communityLoad').disabled")
   link=page.locator('#communityLink');save=page.locator('#communitySubmit')
   link.fill('310');page.wait_for_timeout(80);link.fill('https://civitai.red/images/3101')
   assert 'Preparing artwork preview' in page.locator('#communityDialogMessage').inner_text()
   assert page.locator('#communityPreviewSpinner').is_visible() and save.is_disabled()
   page.wait_for_timeout(150);assert not calls and not writes
   page.wait_for_function("()=>document.querySelector('#communityLoad').disabled")
   assert calls==['https://civitai.red/images/3101'] and not writes,calls
   assert page.locator('#communityPreviewPanel').get_attribute('aria-busy')=='true'
   if width==1440:page.screenshot(path=str(test_safety.REPORTS/'preview-loading-desktop.png'))
   reply(held.pop(),{'artwork':artwork(3101),'quota':{'used':0,'remaining':3,'limit':3}})
   page.locator('#communityPreview .creator-card').wait_for();page.wait_for_timeout(150)
   assert save.is_disabled() and not writes
   assert 'Loading artwork preview' in page.locator('#communityDialogMessage').inner_text()
   assert media
   media.pop().fulfill(content_type='image/svg+xml',body=svg)
   page.wait_for_function("()=>!document.querySelector('#communitySubmit').disabled")
   assert page.locator('#communityPreviewPanel').get_attribute('aria-busy')=='false'
   # A preview removed by the shared loader must never leave confirmation enabled.
   page.evaluate("()=>{const c=document.querySelector('#communityPreview .creator-card');c.remove();c.onUnavailableCardRemoved();}")
   assert save.is_disabled() and 'could not load' in page.locator('#communityDialogMessage').inner_text()
   page.locator('#communityLoad').click();page.wait_for_timeout(100);assert held
   reply(held.pop(),{'artwork':artwork(3101),'quota':{'used':0,'remaining':3,'limit':3}})
   page.wait_for_timeout(150);media.pop().fulfill(content_type='image/svg+xml',body=svg)
   page.wait_for_function("()=>!document.querySelector('#communitySubmit').disabled")
   preview=page.locator('#communityPreviewPanel').bounding_box();fields=page.locator('.recommendation-fields').bounding_box()
   if width>620:assert preview['x']+preview['width']<=fields['x']
   else:assert preview['y']>=fields['y']+fields['height']
   assert page.locator('.community-recommend-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
   save.scroll_into_view_if_needed();assert save.is_visible()
   page.screenshot(path=str(test_safety.REPORTS/f'preview-ready-{width}.png'))
   # Replacing a link aborts an in-flight preview; its late result cannot be saved.
   link.fill('3102');assert save.is_disabled() and page.locator('#communityPreview').is_hidden()
   page.wait_for_timeout(650);old=held.pop();link.fill('3103')
   try:reply(old,{'artwork':artwork(3102)})
   except Error:pass
   page.wait_for_timeout(650);assert held
   reply(held.pop(),{'artwork':artwork(3103),'quota':{'used':0,'remaining':3,'limit':3}})
   page.wait_for_timeout(150);assert save.is_disabled()
   assert page.locator('#communityPreview [data-id="3102"]').count()==0
   media.pop().fulfill(content_type='image/svg+xml',body=svg)
   page.wait_for_function("()=>!document.querySelector('#communitySubmit').disabled")
   assert page.locator('#communityPreview [data-id="3103"]').count()==1 and not writes
   # Arbitrary URLs never start automatic verification or media work.
   count=len(calls);link.fill('https://example.com/not-civitai');page.wait_for_timeout(650)
   assert len(calls)==count and save.is_disabled() and not writes
   # Closing before debounce cancels work, rather than spending background requests.
   link.fill('3104');page.locator('#communityCancel').click();page.wait_for_timeout(650)
   assert len(calls)==count and not writes
   page.locator('#communityRecommend').click();page.wait_for_function("()=>!document.querySelector('#communityLoad').disabled")
   link.fill('3105');page.wait_for_timeout(650);reply(held.pop(),{'code':'INVALID_ARTWORK','error':'Artwork unavailable.'},400)
   page.locator('#communityDialogMessage').filter(has_text='Artwork unavailable.').wait_for()
   assert save.is_disabled() and page.locator('#communityPreviewPanel').get_attribute('aria-busy')=='false'
   # Explicit retry recovers; only the final button submits a reviewed integer ID.
   page.locator('#communityLoad').click();page.wait_for_timeout(100);assert held
   reply(held.pop(),{'artwork':artwork(3105),'quota':{'used':0,'remaining':3,'limit':3}})
   page.wait_for_timeout(150);media.pop().fulfill(content_type='image/svg+xml',body=svg)
   page.wait_for_function("()=>!document.querySelector('#communitySubmit').disabled")
   assert not writes;save.click();page.wait_for_function("()=>!document.querySelector('.community-recommend-dialog').open")
   assert len(writes)==1 and writes[0]['artwork']==3105 and len(writes[0]['idempotencyKey'])>=16
   page.locator('#communityGrid .community-card[data-id="3105"]').wait_for()
   assert page.locator('#communityGrid .community-credit').inner_text().startswith('Recommended by Synthetic')
   assert 'temporarily unavailable' not in page.locator('#communityMessage').inner_text()
   assert not errors,errors
   results.append({'width':width,'automaticDebounce':True,'loadingAndMediaGating':True,'editedURLAndLateResultSafe':True,'invalidURLNoRequest':True,'closeCancelsScheduledWork':True,'retryPassed':True,'explicitConfirmationWrites':1,'writesAreMocked':True,'layoutPassed':True})
   page.close()
  browser.close()
finally:http.shutdown();http.server_close()
(test_safety.REPORTS/'automatic-preview.json').write_text(json.dumps({'passed':True,'cases':results,'hostedRequests':0},indent=2))
print('PASS: automatic preview/loading, stale-response safety, retry, explicit-only confirmation; desktop/mobile/narrow, synthetic services')

"""Hidden-rating confirmations submit explicitly without creating/fetching media."""
import test_safety
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile,threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture
from discovery.community import CommunityClient,ContentProfile,FixtureVerifier,LocalCommunityBackend,CommunityError

http=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=http.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{http.server_port}'
try:
 with tempfile.TemporaryDirectory(prefix='cad3-hidden-preview-') as folder,sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);counter=0
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':1000});install_fixture(page,base,cached_tags=True)
   current={};writes=[];settings=[];media=[];errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   def response(route,operation):
    try:
     payload=route.request.post_data_json if operation in ('verify','recommend') else {}
     if operation=='recommend':writes.append(payload)
     result=current['client'].request(operation,payload)
     if operation=='status':result['beta']={'available':True,'submissions':True}
     route.fulfill(json=result)
    except CommunityError as error:route.fulfill(status=error.status,json=error.response())
   page.route('**/api/community/beta',lambda r:r.fulfill(json={'production':True,'serverPolicy':True,'enabled':True}))
   page.route('**/api/community/status*',lambda r:response(r,'status'))
   page.route('**/api/community/gallery*',lambda r:response(r,'gallery'))
   page.route('**/api/community/verify',lambda r:response(r,'verify'))
   page.route('**/api/community/recommend',lambda r:response(r,'recommend'))
   def setting(route):
    if route.request.method=='GET':return route.fallback()
    settings.append(route.request.method);route.fulfill(json={})
   page.route('**/api/settings',setting)
   page.route('https://image.civitai.com/hidden-rating/**',lambda r:(media.append(r.request.url),r.abort()))
   for kind in ('image','video'):
    for level in (4,8,16):
     counter+=1;verifier=FixtureVerifier(development=True)
     verifier.artworks[1001]=replace(verifier.artworks[1001],rating=level,media_type=kind,preview=f'https://image.civitai.com/hidden-rating/{level}/art.'+('mp4' if kind=='video' else 'jpeg'))
     backend=LocalCommunityBackend(Path(folder)/f'{counter}.sqlite3',verifier)
     current['client']=CommunityClient(backend,verifier,lambda:'fixture:1',lambda _:ContentProfile())
     if counter in (1,7):
      page.goto(base,wait_until='domcontentloaded');page.locator('#tabCommunity').click()
     page.locator('#communityRecommend').click();page.locator('#communityLink').fill('1001')
     page.wait_for_function('()=>!document.querySelector("#communitySubmit").disabled')
     assert 'hidden by your rating settings' in page.locator('#communityDialogMessage').inner_text()
     assert page.locator('#communityPreview img, #communityPreview video, #communityPreview a').count()==0
     assert page.locator('#communityPreviewPlaceholder').is_visible()
     assert backend.request('status','fixture:1',{})['quota']['used']==0
     assert len(writes)==(counter-1)%6 and not media
     if level==4 and kind=='image':page.locator('.community-recommend-dialog').screenshot(path=str(test_safety.REPORTS/f'hidden-rating-confirmation-{width}.png'))
     page.locator('#communitySubmit').click();page.locator('.community-recommend-dialog').wait_for(state='hidden')
     assert backend.request('status','fixture:1',{})['quota']['used']==1
     assert page.locator('#communityGrid .creator-card').count()==0,'Submitting must not override the PG/PG-13 gallery filter'
   assert not media and not settings and not errors,(media,settings,errors)
   page.close()
  browser.close()
 print('PASS: R/X/XXX image/video, desktop/mobile blind confirmation, zero hidden media, explicit submission, quota and gallery filtering preserved; synthetic services')
finally:http.shutdown();http.server_close()

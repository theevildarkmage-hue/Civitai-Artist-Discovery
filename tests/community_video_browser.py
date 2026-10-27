"""Real browser media decoder in Community cards/details; synthetic isolated feed."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json,threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture
server=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900});errors=[];writes=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('request',lambda r:writes.append(r.url) if r.method=='POST' and '/api/community/recommend' in r.url else None)
   install_fixture(page,base,cached_tags=True)
   reply=lambda route,value:route.fulfill(content_type='application/json',body=json.dumps(value))
   page.route('**/community-test.mp4',lambda r:r.fulfill(content_type='video/mp4',body=(ROOT/'tests/fixtures/recovery.mp4').read_bytes()))
   # ROOT from visual fixtures is the repository root; no external CDN requests.
   art={'id':123,'creatorId':42,'username':'SyntheticVideoArtist','type':'video','baseModel':'YuE2-3B','modelLabels':['YuE2-3B'],'browsingLevel':1,'url':f'{base}/community-fixture.svg','thumbnailUrl':f'{base}/community-fixture.svg','videoUrl':f'{base}/community-test.mp4','detailVideoUrl':f'{base}/community-test.mp4','imageUrl':'https://civitai.red/images/123','tagState':{'known':True,'tags':[]}}
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True}))
   page.route('**/api/community/config',lambda r:reply(r,{'development':False}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':7},'quota':{'remaining':3},'beta':{'available':True,'submissions':True}}))
   page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[{'artwork':art,'recommender':'SyntheticMember','submittedAt':'2026-10-24T12:00:00Z'}],'nextCursor':None}))
   page.route('**/api/community/detail*',lambda r:reply(r,{**art,'detailImageUrl':art['thumbnailUrl'],'civitaiUrl':art['imageUrl'],'known':True,'tags':[],'prompt':''}))
   page.goto(base,wait_until='domcontentloaded')
   muted=width==1440
   if muted:
    # Turning sound off is saved like the other gallery preferences.
    page.locator('#galleryPreferences').click()
    sound=page.locator('#prefDetailVideoSound');assert sound.is_checked();sound.uncheck()
    page.wait_for_function('()=>!document.querySelector("#prefDetailVideoSound").checked&&!document.querySelector("#prefDetailVideoSound").disabled')
    # It sits directly after the viewed-card dimming checkbox.
    assert page.evaluate('document.querySelector("#prefDimSeen").closest("label").nextElementSibling.contains(document.querySelector("#prefDetailVideoSound"))')
    page.keyboard.press('Escape')
   page.locator('#tabCommunity').click()
   card=page.locator('#communityGrid .creator-card');card.wait_for()
   page.wait_for_function('()=>[...document.querySelectorAll("#communityGrid video")].some(v=>v.readyState>=2&&v.currentTime>0)',timeout=30000)
   card.locator('.info-button').click()
   page.wait_for_function('()=>document.querySelector("#detailVideo")?.currentTime>0',timeout=30000)
   # Details play with sound by default; turning the preference off starts them muted.
   assert page.evaluate('document.querySelector("#detailVideo").muted')==muted,width
   page.locator('#detailMeta dt').filter(has_text='Video').wait_for()
   page.locator('#close').click()
   page.wait_for_function('()=>!document.querySelector("#detailVideo")')
   page.locator('#filterToggle').click();page.locator('#mediaType').select_option('image')
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .creator-card").length===0')
   page.locator('#mediaType').select_option('video')
   card.wait_for();assert not errors and not writes,(errors,writes)
   page.screenshot(path=str(test_safety.REPORTS/f'community-video-{width}.png'))
   page.close()
  browser.close()
 print('PASS: desktop/mobile Community video decoding, details with sound or muted by preference, cleanup and media filtering; synthetic artwork, no hosted writes')
finally:server.shutdown();server.server_close()

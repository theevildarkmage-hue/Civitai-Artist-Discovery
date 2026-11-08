"""Production ID-feed notices count only locally admitted art and offer a refresh action."""
import test_safety
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  page=browser.new_page(extra_http_headers=h.headers());page.clock.install()
  state={'ids':[1001]};reads=[];errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('**/api/auth-status',lambda r:r.fulfill(json={'connected':True,'id':42,'username':'Fixture'}))
  page.route('**/api/discovery/summary',lambda r:r.fulfill(json={'hasData':False}))
  page.route('**/api/creator-metadata?*',lambda r:r.fulfill(json={'creators':{}}))
  page.route('**/api/reaction-status?*',lambda r:r.fulfill(json={'images':{}}))
  page.route('**/api/community/beta',lambda r:r.fulfill(json={'production':True,'serverPolicy':True,'enabled':True}))
  page.route('**/api/community/status*',lambda r:r.fulfill(json={'member':{'id':42},'quota':{'remaining':3},'beta':{'available':True,'submissions':True}}))
  def gallery(route):
   route.fulfill(json={'feedVersion':3,'items':[{'imageId':i,'creatorId':i+10000,'submittedAt':'2026-11-07T12:00:00Z' if i==1001 else '2026-11-07T13:00:00Z','recommender':'Fixture'} for i in state['ids']], 'nextCursor':None,'cache':{'state':'current'}})
  page.route('**/api/community/gallery?*',gallery)
  def prepare(route):
   ids=route.request.post_data_json['imageIds'];reads.extend(ids)
   # 1003 is hidden; 1004 is unavailable. Neither is eligible for this account.
   images=[{'id':i,'creatorId':i+10000,'username':f'Artist{i}','url':f'/community-fixture.svg?id={i}',
            'thumbnailUrl':f'/community-fixture.svg?id={i}','type':'image','tagState':{'known':True,'tags':[]}} for i in ids if i not in (1003,1004)]
   route.fulfill(json={'images':images,'counts':{'contentControls':int(1003 in ids),'unavailable':int(1004 in ids)}})
  page.route('**/api/history/images',prepare)
  page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
  page.wait_for_function('()=>document.querySelectorAll("#communityGrid .community-card").length===1')
  page.evaluate('window.__first=document.querySelector("#communityGrid .community-card")')
  state['ids']=[1002,1002,1003,1004,1001]
  page.clock.fast_forward(60000)
  try:page.wait_for_function('()=>document.querySelector("#communityUpdates").textContent.startsWith("1 new recommendation")',timeout=5000)
  except Exception:
   print({'reads':reads,'errors':errors,'notice':page.locator('#communityUpdates').inner_text(),'message':page.locator('#communityMessage').inner_text()},flush=True)
   raise
  assert {1002,1003,1004}.issubset(reads)
  assert page.evaluate('window.__first===document.querySelector("#communityGrid .community-card")')
  page.locator('.community-heading').screenshot(path=str(test_safety.REPORTS/'production-new-art-banner.png'))
  page.locator('#communityUpdates button').click()
  page.wait_for_function('()=>document.querySelectorAll("#communityGrid .community-card").length===2 && document.querySelector("#communityUpdates").textContent===""')
  assert page.locator('#communityGrid [data-id="1003"]').count()==0
  assert page.locator('#communityGrid [data-id="1004"]').count()==0
  assert not errors,errors
  page.screenshot(path=str(test_safety.REPORTS/'production-feed-after-refresh.png'))
  browser.close()
 print('PASS: production ID feed, eligible count, hidden/unavailable exclusion, deduplication, stable cards, actionable refresh; synthetic provider only')
finally:h.stop()

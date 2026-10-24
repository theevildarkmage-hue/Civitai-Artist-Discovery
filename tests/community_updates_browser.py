"""New-recommendation notices without feed/scroll mutation; isolated synthetic HTTP."""
import test_safety,json,socket
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from community_harness import Harness
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
def row(number,new=False):
    art={'id':number,'creatorId':number+100000,'username':f'Artist{number}',
         'profileUrl':f'https://civitai.red/user/Artist{number}','imageUrl':f'https://civitai.red/images/{number}',
         'url':f'/community-fixture.svg?id={number}','thumbnailUrl':f'/community-fixture.svg?id={number}',
         'type':'image','tagState':{'known':True,'tags':[]}}
    return {'artwork':art,'recommender':'NewMember' if new else 'OldMember','submittedAt':'2026-10-25T12:00:00Z' if new else '2026-10-23T12:00:00Z'}
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers());page.clock.install()
   state={'new':[],'fail':False,'hold':False};calls=[];held=[];errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
   page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'Fixture'}))
   page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
   page.route('**/api/creator-metadata?*',lambda r:reply(r,{'creators':{}}))
   page.route('**/api/reaction-status?*',lambda r:reply(r,{'images':{}}))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True,'enabled':True}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':42},'quota':{'remaining':3,'used':0,'limit':3},'beta':{'available':True,'submissions':True}}))
   original=[row(10000+i) for i in range(60)]
   restored=[]
   def gallery(route):
    q=parse_qs(urlsplit(route.request.url).query);offset=int(q.get('cursor',['0'])[0]);calls.append(offset)
    data=state['new']+original[:2]+restored+original[2:];response={'items':data[offset:offset+24],'nextCursor':str(offset+24) if offset+24<len(data) else None}
    if state['hold']:state['hold']=False;held.append((route,response));return
    if state['fail']:return reply(route,{'code':'UNAVAILABLE','error':'Synthetic outage'},503)
    reply(route,response)
   page.route('**/api/community/gallery?*',gallery)
   page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
   page.wait_for_function('()=>document.querySelectorAll("#communityGrid .community-card").length===24')
   assert page.locator('#communityUpdates').inner_text()==''
   page.evaluate('scrollTo(0,800)');page.wait_for_timeout(50)
   saved=page.evaluate('scrollY')
   page.evaluate('window.__noticeCards=[...document.querySelectorAll("#communityGrid .community-card")]')
   state['new']=[row(20003,True),row(20003,True),row(20002,True),row(20001,True)]
   page.clock.fast_forward(60000)
   try:page.wait_for_function('()=>document.querySelector("#communityUpdates").textContent.startsWith("3 new recommendations")',timeout=10000)
   except Exception:
    print({'calls':calls,'errors':errors,'browser':page.evaluate('()=>({notice:document.querySelector("#communityUpdates").textContent,message:document.querySelector("#communityMessage").textContent,filters:galleryFilterState()})')},flush=True)
    raise
   assert page.locator('#communityUpdates').inner_text()=='3 new recommendations \u2014 Refresh to Show'
   assert page.evaluate('()=>{const now=[...document.querySelectorAll("#communityGrid .community-card")];return now.length===window.__noticeCards.length&&now.every((el,i)=>el===window.__noticeCards[i])}')
   assert abs(page.evaluate('scrollY')-saved)<2
   page.screenshot(path=str(test_safety.REPORTS/f'new-recommendations-{width}.png'),full_page=True)
   # Background outage preserves both the notice and existing browsing state.
   state['fail']=True;page.clock.fast_forward(60000);page.wait_for_timeout(100)
   assert page.locator('#communityUpdates').inner_text().startswith('3 new recommendations')
   assert abs(page.evaluate('scrollY')-saved)<2;state['fail']=False
   # Tab hidden: no gallery probes. Returning checks without replacing cards.
   page.locator('#tabGallery').click();count=len(calls);page.clock.fast_forward(60000);page.wait_for_timeout(50);assert len(calls)==count
   page.locator('#tabCommunity').click();page.wait_for_timeout(100)
   assert page.locator('#communityGrid .community-card').first.get_attribute('data-id')=='10000'
   # Refresh cancels an in-flight old probe; its late result cannot restore notice.
   state['hold']=True;page.clock.fast_forward(60000)
   for _ in range(100):
    if held:break
    page.wait_for_timeout(10)
   assert held
   page.locator('#communityRefresh').evaluate('el=>el.click()')
   page.wait_for_function('()=>document.querySelector("#communityGrid .community-card")?.dataset.id==="20003"')
   page.wait_for_function('()=>document.querySelector("#communityUpdates").textContent==="" && scrollY===0')
   for route,response in held:
    try:reply(route,response)
    except Exception:pass
   held.clear();page.wait_for_timeout(50);assert page.locator('#communityUpdates').inner_text()==''
   assert page.locator('#communityGrid .community-card[data-id="20003"]').count()==1
   # An older recommendation becomes available again behind known head rows.
   # It must announce availability, without changing the existing cards/scroll.
   recovered=row(15000);recovered['submittedAt']='2026-10-24T12:00:00Z'
   restored.append(recovered)
   page.evaluate('window.__noticeCards=[...document.querySelectorAll("#communityGrid .community-card")]');saved=page.evaluate('scrollY')
   page.clock.fast_forward(60000)
   page.wait_for_function('()=>document.querySelector("#communityUpdates").textContent.startsWith("1 recommendation available")')
   assert page.evaluate('()=>{const now=[...document.querySelectorAll("#communityGrid .community-card")];return now.length===window.__noticeCards.length&&now.every((el,i)=>el===window.__noticeCards[i])}')
   assert abs(page.evaluate('scrollY')-saved)<2
   restored.clear()
   # A large burst uses bounded reads and an honest lower-bound count.
   state['new']=[row(30000+i,True) for i in range(120)]+state['new']
   # Make the burst strictly newer than the displayed recommendations.
   for item in state['new'][:120]:item['submittedAt']='2026-10-26T12:00:00Z'
   count=len(calls);page.clock.fast_forward(60000)
   page.wait_for_function('()=>document.querySelector("#communityUpdates").textContent.startsWith("96+")')
   assert len(calls)-count==4
   assert page.locator('#communityGrid .community-card').first.get_attribute('data-id')=='20003'
   assert not errors,errors
   page.close()
  browser.close()
 print('PASS: desktop/mobile notices, count deduplication, unchanged DOM/scroll and paging, hidden-tab pause, transient outage, refresh newest-first/top, late-probe cancellation, bounded96+ count; synthetic HTTP only')
finally:h.stop()

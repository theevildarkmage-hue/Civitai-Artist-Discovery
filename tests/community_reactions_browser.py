"""Initial/grouped Community reaction hydration, retry, cross-card updates and account isolation."""
import test_safety
import socket
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from community_harness import Harness
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);page=browser.new_page(extra_http_headers=h.headers());page.clock.install()
  state={'account':42,'fail':True,'hold':False,'ids':[1001]};reads=[];writes=[];held=[]
  page.route('**/api/auth-status',lambda r:r.fulfill(json={'connected':True,'id':state['account'],'username':'Fixture','socialWrite':True}))
  page.route('**/api/discovery/summary',lambda r:r.fulfill(json={'hasData':False}))
  page.route('**/api/creator-metadata?*',lambda r:r.fulfill(json={'creators':{}}))
  page.route('**/api/community/beta',lambda r:r.fulfill(json={'production':True,'serverPolicy':True,'enabled':True}))
  page.route('**/api/community/status*',lambda r:r.fulfill(json={'member':{'id':state['account']},'quota':{'remaining':3},'beta':{'available':True,'submissions':True}}))
  def art(i):
   return {'id':i,'creatorId':9000,'username':'SyntheticArtist','url':f'/community-fixture.svg?id={i}','thumbnailUrl':f'/community-fixture.svg?id={i}',
           'type':'image','stats':{'likeCount':6,'reactionCount':11},'tagState':{'known':True,'tags':[]}}
  page.route('**/api/community/gallery?*',lambda r:r.fulfill(json={'items':[{'artwork':art(i),'submittedAt':'2026-11-07T12:00:00Z','recommender':'Fixture'} for i in state['ids']],'nextCursor':None}))
  def status(route):
   ids=parse_qs(urlsplit(route.request.url).query)['imageId'];reads.extend(ids)
   response={'images':{i:{'reactions':['Like','Heart','Laugh','Cry'] if state['account']==42 else [],'stats':{'likeCount':6,'reactionCount':11}} for i in ids}}
   if state['hold']:held.append((route,response));return
   if state['fail']:route.fulfill(status=503,json={'error':'synthetic outage'});return
   route.fulfill(json=response)
  page.route('**/api/reaction-status?*',status)
  def mutate(route):
   writes.append(route.request.post_data_json);route.fulfill(json={'reactions':[],'stats':{'likeCount':5,'reactionCount':10},'changed':True})
  page.route('**/api/reaction',mutate)
  page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
  card=page.locator('#communityGrid .community-card');card.wait_for();page.wait_for_timeout(100)
  assert writes==[]
  assert '1001' in reads,'initial supplied-image card must request its reaction state'
  assert card.locator('.reaction-bar').get_attribute('data-state-known')=='false'
  # A later activation retries a failed read without treating failure as no reactions.
  state['fail']=False;page.evaluate('window.CivitaiCommunity.activate()')
  page.wait_for_function('()=>document.querySelectorAll("#communityGrid [data-reaction].selected").length===4',timeout=5000)
  assert card.locator('.total-reactions').inner_text()=='Total 11'
  assert writes==[]
  state['ids']=[1001,1002];page.evaluate('window.CivitaiCommunity.load(false,true)')
  page.wait_for_function('()=>document.querySelector("#communityGrid .image-position").textContent.includes("of 2")')
  page.wait_for_timeout(100)
  assert {'1001','1002'}.issubset(reads)
  card.locator('.next').click();page.wait_for_function('()=>document.querySelector("#communityGrid .community-card").dataset.id==="1002"')
  assert card.locator('[data-reaction].selected').count()==4
  # A second shared card observes the same mutation through the common state map.
  page.evaluate('''()=>{const a={username:'SyntheticArtist',userId:9000,imageCount:1,representative:{id:1002,type:'image',stats:{},tagState:{known:true,tags:[]}}};
    const c=window.DiscoveryGallery.createCard(a,{images:[a.representative],seenObserver:{observe(){},unobserve(){}}});c.id='reactionMirror';document.querySelector('main').append(c);}''')
  card.locator('[data-reaction="Like"]').click()
  page.wait_for_function('()=>!document.querySelector("#reactionMirror [data-reaction=Like]").classList.contains("selected")')
  assert len(writes)==1 and writes[0]['active'] is False
  # A slow hydration read must not overwrite a newer completed user action.
  state['hold']=True
  page.evaluate('''()=>{const image={id:1005,type:'image',stats:{},tagState:{known:true,tags:[]}};
    const c=window.DiscoveryGallery.createCard({username:'RaceArtist',userId:9001,imageCount:1,representative:image},
      {images:[image],seenObserver:{observe(){},unobserve(){}}});c.id='reactionRace';document.querySelector('main').append(c);
    void window.DiscoveryGallery.hydrateReactionStates([image]);}''')
  page.wait_for_timeout(50);assert held
  page.locator('#reactionRace [data-reaction="Like"]').click()
  page.wait_for_function('()=>document.querySelector("#reactionRace .reaction-bar").dataset.stateKnown==="true"')
  state['hold']=False
  for route,response in held:route.fulfill(json=response)
  held.clear();page.wait_for_timeout(100)
  assert page.locator('#reactionRace [data-reaction].selected').count()==0,'old read overwrote completed reaction'
  # Start an old-account request, switch account through the real auth poll, then
  # release its reply; it must never restore the previous account's selections.
  page.evaluate('window.DiscoveryGallery.hydrateReactionStates([{id:1003,stats:{}}])');page.wait_for_timeout(50)
  state['hold']=True
  page.evaluate('void window.DiscoveryGallery.hydrateReactionStates([{id:1004,stats:{}}])');page.wait_for_timeout(50)
  assert held
  state['account']=43;page.clock.fast_forward(300000)
  page.wait_for_function('()=>document.querySelector("#accountStatus").textContent.includes("Fixture")')
  page.wait_for_timeout(100)
  state['hold']=False
  for route,response in held:
   try:route.fulfill(json=response)
   except Exception:pass
  page.wait_for_timeout(100)
  assert page.locator('#reactionMirror [data-reaction].selected').count()==0
  page.evaluate('window.DiscoveryGallery.hydrateReactionStates([{id:1004,stats:{}}])');page.wait_for_timeout(100)
  assert reads.count('1004')>=2,'late previous-account reply must not populate the new account cache'
  assert writes and len(writes)==2
  browser.close()
 print('PASS: grouped hydration/retry, four reaction kinds, aggregate counts, shared update, account clearing and late replies; synthetic only')
finally:h.stop()

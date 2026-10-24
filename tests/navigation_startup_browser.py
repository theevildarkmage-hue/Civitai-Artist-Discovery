"""Fresh session starts Community; refresh restores validated per-tab navigation."""
import test_safety
import json,socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
   signed=[True];cached=[False];errors=[];day_reads=[];writes=[]
   page.on('pageerror',lambda error:errors.append(str(error)))
   page.on('request',lambda r:writes.append(r.url) if r.method=='POST' and '/api/community/recommend' in r.url else None)
   def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
   page.route('**/api/auth-status',lambda r:reply(r,{'connected':signed[0],'cachedSession':cached[0],'id':42 if signed[0] else None,'username':'SyntheticNavigation'}))
   page.route('**/api/discovery/status',lambda r:reply(r,{'running':False}))
   page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'available':True,'enabled':True,'serverPolicy':True}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':42},'quota':{'remaining':3},'beta':{'available':True,'submissions':False}}))
   page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[],'nextCursor':None}))
   page.route('**/api/timemachine',lambda r:reply(r,{'cards':[],'status':{}}))
   def day(route):
    day_reads.append(route.request.url)
    reply(route,{'state':'not_started','complete':False,'archiveComplete':False,'itemCount':0})
   page.route('**/api/history/status?*',day)
   page.goto(h.base,wait_until='domcontentloaded')
   page.wait_for_function('()=>document.querySelector("#tabCommunity")?.getAttribute("aria-pressed")==="true"')
   assert page.locator('.view-tabs > button').first.get_attribute('id')=='tabCommunity'
   assert not day_reads,'Community startup must not load the Daily archive'
   assert page.locator('#contentFilter,#contentPanel').count()==0
   page.locator('#filterToggle').click()
   assert page.locator('#filterPanel #contentMenu [data-level]').count()==5
   page.keyboard.press('Escape')
   for tab,view in [('tabDiscovery','discovery'),('tabTimeMachine','timemachine'),('tabGallery','gallery'),('tabCommunity','community')]:
    page.locator('#'+tab).click()
    page.wait_for_function('(id)=>document.getElementById(id)?.getAttribute("aria-pressed")==="true"',arg=tab)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('(id)=>document.getElementById(id)?.getAttribute("aria-pressed")==="true"',arg=tab)
    assert page.evaluate('sessionStorage.getItem("civitai-active-view")')==view
   page.evaluate('sessionStorage.setItem("civitai-active-view","invalid-view")')
   page.reload(wait_until='domcontentloaded')
   page.wait_for_function('()=>document.querySelector("#tabCommunity")?.getAttribute("aria-pressed")==="true"')
   signed[0]=False;page.reload(wait_until='domcontentloaded')
   page.locator('#welcome:not(.hidden)').wait_for()
   assert page.locator('.view-tabs').is_hidden()
   cached[0]=True;page.reload(wait_until='domcontentloaded')
   page.wait_for_function('()=>document.querySelector("#tabGallery")?.getAttribute("aria-pressed")==="true"')
   assert page.locator('#welcome').is_hidden()
   assert not errors and not writes,(errors,writes)
   page.close()
  browser.close()
 print('PASS: desktop/mobile first Community, fresh default, all four tab refreshes, invalid-state fallback, signed-out gate and single Filters; synthetic identity/read HTTP, no submissions')
finally:h.stop()

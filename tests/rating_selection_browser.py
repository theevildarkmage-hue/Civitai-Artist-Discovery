"""Actual application controls with delayed synthetic settings HTTP responses."""
import test_safety,json,socket
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from community_harness import Harness
from v2_visual_baseline import install_fixture,DAY
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
   install_fixture(page,h.base)
   saved={'browsingLevels':[1,2],'contentRating':'Soft'};held=[];posts=[];days=[];fail=[False];hold_days=[False];held_days=[]
   def respond(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
   def settings(route):
    if route.request.method=='POST':
     value=route.request.post_data_json;posts.append(value)
     if fail[0]:fail[0]=False;return respond(route,{'error':'Synthetic save failure'},503)
     held.append((route,value));return
    respond(route,{**saved,'mediaType':'all','checkForUpdates':False})
   page.route('**/api/settings',settings)
   def day_response(route):
    days.append(route.request.url)
    if hold_days[0]:held_days.append(route)
    else:route.fallback()
   page.route('**/api/history/day?*',day_response)
   page.goto(h.base,wait_until='networkidle');page.evaluate('(day)=>loadDay(day)',DAY)
   page.locator('#filterToggle').click()
   def click(levels):
    page.evaluate('(levels)=>levels.forEach(level=>document.querySelector(`#contentMenu [data-level="${level}"]`).click())',levels)
   def release():
    route,value=held.pop(0);saved.update(value)
    saved['contentRating']='X' if any(n>=8 for n in saved['browsingLevels']) else 'Mature' if 4 in saved['browsingLevels'] else 'Soft'
    respond(route,saved.copy())
   before=len(days)
   click([16,4,1,8])
   assert page.locator('#filterToggle').is_enabled()
   assert page.locator('#contentFilter,#contentPanel').count()==0
   assert page.locator('#contentMenu button[data-level]:disabled').count()==0
   page.wait_for_function('()=>document.querySelector("#contentMenu").getAttribute("aria-busy")==="true"')
   for _ in range(100):
    if held:break
    page.wait_for_timeout(10)
   assert len(posts)==1 and set(posts[0]['browsingLevels'])=={2,4,8,16},posts
   # More clicks while the first HTTP save is deliberately blocked. No stale
   # response may restore removed levels or start an intermediate gallery load.
   click([16,8,1]);assert len(days)==before
   release()
   for _ in range(100):
    if held:break
    page.wait_for_timeout(10)
   assert len(posts)==2 and set(posts[1]['browsingLevels'])=={1,2,4}
   assert set(page.evaluate('galleryFilterState().levels'))=={1,2,4}
   assert len(days)==before
   release()
   page.wait_for_function('()=>document.querySelector("#contentMenu").getAttribute("aria-busy")==="false"')
   page.wait_for_timeout(500)
   assert len(days)==before+1,(days,before)
   assert set(saved['browsingLevels'])=={1,2,4}
   fail[0]=True;click([4]);page.wait_for_timeout(600)
   page.wait_for_function('()=>document.querySelector("#contentMenu").getAttribute("aria-busy")==="false"')
   assert set(page.evaluate('galleryFilterState().levels'))=={1,2,4}
   # A second selection during an in-flight gallery read must cancel that
   # read and still initiate the final reload (dayBuilt is temporarily false).
   hold_days[0]=True;click([16])
   for _ in range(100):
    if held:break
    page.wait_for_timeout(10)
   release()
   for _ in range(100):
    if held_days:break
    page.wait_for_timeout(10)
   assert held_days,'Expected held gallery reload'
   click([8])
   hold_days[0]=False
   held_days.pop(0).fallback()
   for _ in range(100):
    if held:break
    page.wait_for_timeout(10)
   release()
   page.wait_for_function('()=>dayBuilt && document.querySelector("#contentMenu").getAttribute("aria-busy")==="false"')
   assert set(saved['browsingLevels'])=={1,2,4,8,16}
   assert set(page.evaluate('galleryFilterState().levels'))=={1,2,4,8,16}
   keys=page.evaluate('''async()=>{
     const {requestKey}=await import('/ui/request-key.js');
     const native=requestKey();
     const fallback=requestKey({getRandomValues:bytes=>{bytes.fill(17);return bytes;}});
     let denied=false;try{requestKey({});}catch{denied=true;}
     return {native,fallback,denied};
   }''')
   import re
   pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
   assert re.match(pattern,keys['native']) and re.match(pattern,keys['fallback']) and keys['denied']
   page.screenshot(path=str(test_safety.REPORTS/f'ratings-{width}.png'))
   page.close()
  browser.close()
 print('PASS: desktop/mobile immediate unordered selections, debounce, serialized latest intent, no stale reply or intermediate reload, failure re-read/rollback; synthetic HTTP')
finally:h.stop()

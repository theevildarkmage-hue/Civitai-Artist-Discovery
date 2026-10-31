"""Cosmetic shell checks only; auth/data supplied by isolated fixtures."""
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
  for width,height in ((1440,900),(768,1024),(390,900),(320,800)):
   page=browser.new_page(viewport={'width':width,'height':height},extra_http_headers=h.headers())
   def reply(route,value):route.fulfill(content_type='application/json',body=json.dumps(value))
   page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':17,'username':'Synthetic','socialWrite':True,'cachedSession':True,'communityAuthorization':{'state':'ready','action':'none'}}))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True}))
   page.route('**/api/community/config',lambda r:reply(r,{'development':False}))
   page.route('**/api/community/status*',lambda r:reply(r,{'member':{'id':17},'quota':{'remaining':3},'beta':{'available':True,'submissions':True}}))
   page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[],'nextCursor':None}))
   page.goto(h.base,wait_until='networkidle')
   page.get_by_role('button',name='Community Beta',exact=True).wait_for()
   page.wait_for_function('()=>document.querySelector(".app-logo").complete&&document.querySelector(".app-logo").naturalWidth>0')
   assert page.locator('h1').inner_text()=='Civitai Discovery 3.0 Beta'
   assert page.locator('#appVersion').inner_text().startswith('v3.')
   assert page.locator('.app-logo').get_attribute('alt')==''
   for tab in ('tabCommunity','tabGallery','tabTimeMachine','tabCollectalytics','tabDiscovery'):
    assert page.locator('#'+tab+' svg.ui-icon[aria-hidden="true"]').count()==1
   sizes=page.locator('.view-tabs > button > svg').evaluate_all('(xs)=>xs.map(x=>[getComputedStyle(x).width,getComputedStyle(x).height])')
   assert len(sizes)==5 and len({tuple(x) for x in sizes})==1,sizes
   page.locator('#tabGallery').click()
   assert page.locator('#tabGallery').get_attribute('aria-pressed')=='true'
   page.locator('#tabCommunity').click()
   assert page.locator('#tabCommunity').get_attribute('aria-pressed')=='true'
   page.reload(wait_until='networkidle')
   assert page.locator('#tabCommunity').get_attribute('aria-pressed')=='true'
   page.screenshot(path=str(test_safety.REPORTS/f'header-{width}.png'))
   assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll("header,.app-title-row,.view-tabs,.view-tabs>button")].map(x=>[x.className,x.getBoundingClientRect().left,x.getBoundingClientRect().right])})')
   page.close()
  browser.close()
 print('PASS: header/logo and shared tab icons at 1440, 768, 390, 320; accessible labels, navigation and reload preserved')
finally:h.stop()

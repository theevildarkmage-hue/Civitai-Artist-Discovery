"""Actual UI with synthetic HTTP authorization states; no provider login."""
import test_safety
import json,socket
from playwright.sync_api import sync_playwright
from community_harness import Harness
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False);checks=0
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers());ready=[False];posts=[]
   def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
   def auth(route):reply(route,{'connected':True,'cachedSession':True,'id':17,'username':'Synthetic','communityAuthorization':{'state':'ready' if ready[0] else 'incomplete','reason':'missing_identity_proof','action':'none' if ready[0] else 'reconnect'},'oauthJob':{'state':'complete' if ready[0] else 'idle'}})
   page.route('**/api/auth-status',auth)
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'available':True,'enabled':True,'serverPolicy':True}))
   def status(route):
    if not ready[0]:return reply(route,{'code':'AUTH_EXPIRED','error':'Generic authorization error'},401)
    reply(route,{'apiVersion':1,'quota':{'remaining':3},'member':{'id':17},'beta':{'available':True,'submissions':True,'accessMode':'invite_only'}})
   page.route('**/api/community/status*',status)
   page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[],'nextCursor':None}))
   def reconnect(route):posts.append(route.request.post_data);ready[0]=True;reply(route,{'started':True},202)
   page.route('**/api/community/reconnect',reconnect)
   page.goto(h.base,wait_until='domcontentloaded')
   page.locator('#communityReconnect').wait_for(state='visible')
   assert 'Daily' in page.locator('#communityMessage').inner_text()
   page.locator('#communityReconnect').click()
   page.locator('#communityQuota').filter(has_text='3 left').wait_for(timeout=15000)
   page.wait_for_function('()=>document.querySelector("#communityReconnect").hidden')
   assert posts==['{}'];assert page.locator('#tabCommunity').get_attribute('aria-pressed')=='true'
   assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
   page.screenshot(path=str(test_safety.REPORTS/f'reconnect-{width}.png'))
   page.close();checks+=1
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
   page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':17,'username':'Synthetic','communityAuthorization':{'state':'unsupported_client','reason':'unsupported_client','action':'none'},'oauthJob':{'state':'idle'}}))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True}))
   page.route('**/api/community/status*',lambda r:reply(r,{'code':'AUTH_EXPIRED','error':'Generic authorization error'},401))
   page.goto(h.base,wait_until='domcontentloaded')
   page.locator('#communityMessage').filter(has_text='Community requires the built-in application').wait_for()
   assert page.locator('#communityReconnect').is_hidden()
   assert 'Switching applications requires fresh authorization' in page.locator('#communityMessage').inner_text()
   page.locator('#tabGallery').click()
   assert page.locator('#tabGallery').get_attribute('aria-pressed')=='true'
   page.screenshot(path=str(test_safety.REPORTS/f'custom-client-{width}.png'))
   page.close();checks+=1
  browser.close()
 print(json.dumps({'passed':checks,'identityAndProviderSimulated':True,'realOAuthOpened':False}))
finally:h.stop()

"""Real browser UI, synthetic HTTP: Daily card admission and expired-view recovery."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json,threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture

server=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   page=browser.new_page(viewport={'width':width,'height':900});errors=[];writes=[];status_calls=[];held=[];hold=[False];denial=[None];enabled=[True]
   page.on('pageerror',lambda error:errors.append(str(error)))
   page.on('request',lambda r:writes.append(r.url) if r.method=='POST' and '/api/community/recommend' in r.url else None)
   install_fixture(page,base,cached_tags=True)
   def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
   page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'serverPolicy':True,'available':True}))
   page.route('**/api/community/config',lambda r:reply(r,{'development':False}))
   def status(route):
    status_calls.append(True)
    if hold[0]:held.append(route);return
    if denial[0]:reply(route,{'code':denial[0],'error':'Synthetic admission denial'},401 if denial[0]=='AUTH_EXPIRED' else 403)
    else:reply(route,{'member':{'id':7},'quota':{'used':0,'remaining':3,'limit':3},'beta':{'available':True,'submissions':enabled[0]}})
   page.route('**/api/community/status*',status)
   page.route('**/api/community/gallery*',lambda r:reply(r,{'items':[],'nextCursor':None}))
   page.goto(base,wait_until='networkidle')
   page.locator('#gallery .creator-card').first.wait_for()
   assert not status_calls,'Daily card entry must work before visiting Community'
   def card_recommend():
    page.locator('#gallery .more-menu').first.click()
    page.get_by_role('menuitem',name='Recommend to Community').click()
   hold[0]=True;card_recommend()
   page.locator('#communityDialogTitle').wait_for(state='visible')
   assert page.locator('#communityLink').input_value()=='1'
   assert page.locator('#communitySubmit').is_disabled()
   assert page.locator('#communitySubmit').inner_text()=='Load artwork first'
   page.locator('#communityLink').fill('1002')
   hold[0]=False
   reply(held.pop(),{'quota':{'used':0,'remaining':3,'limit':3},'beta':{'available':True,'submissions':True}})
   page.wait_for_function("()=>!document.querySelector('#communityLoad').disabled")
   assert page.locator('#communityLink').input_value()=='1002'
   media=[]
   page.route('**/recommend-preview.svg',lambda route:media.append(route))
   page.route('**/recommend-preview.mp4',lambda route:route.fulfill(content_type='video/mp4',body=(ROOT/'tests/fixtures/recovery.mp4').read_bytes()))
   page.route('**/api/creator-metadata?*',lambda route:reply(route,{'creators':{'previewartist':{'userId':55,'following':True,'followers':20}}}))
   page.route('**/api/community/verify',lambda route:reply(route,{'artwork':{
    'id':1002,'creatorId':55,'username':'PreviewArtist','profileUrl':'https://civitai.red/user/PreviewArtist','tagState':{'known':True,'tags':[]},
    'type':'video' if width==1440 else 'image','videoUrl':base+'/recommend-preview.mp4','browsingLevel':1,'baseModel':'SDXL','url':base+'/recommend-preview.svg',
    'thumbnailUrl':base+'/recommend-preview.svg'}}))
   page.locator('#communityLoad').click()
   page.locator('#communityPreview .image-button img').wait_for(state='attached')
   assert page.locator('#communitySubmit').is_disabled() and not writes
   for _ in range(100):
    if media:break
    page.wait_for_timeout(10)
   reply_route=media.pop()
   reply_route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="blue"/></svg>')
   page.wait_for_function("()=>!document.querySelector('#communitySubmit').disabled")
   page.locator('#communityPreview .follow-button').filter(has_text='Following').wait_for()
   assert page.locator('#communityPreview .image-stage').evaluate('(e)=>e.clientHeight>e.clientWidth')
   if width==1440:
    page.wait_for_function("()=>{const v=document.querySelector('#communityPreview video');return v && v.loop && !v.paused;}")
   page.screenshot(path=str(test_safety.REPORTS/f'recommend-preview-{width}.png'))
   page.locator('#communityLink').fill('1003')
   assert page.locator('#communitySubmit').is_disabled() and page.locator('#communityPreview').is_hidden()
   assert len(status_calls)==1 and not writes
   page.locator('#communityCancel').click()
   # A denial is visible from Daily, rather than a dead card action.
   denial[0]='AUTH_EXPIRED';card_recommend()
   page.locator('#toast').filter(has_text='Reconnect Community').wait_for()
   assert not page.locator('dialog[open]').count()
   page.locator('#tabCommunity').click()
   page.locator('#communityMessage').filter(has_text='Reconnect Community').wait_for()
   page.locator('#communityReconnect').wait_for(state='visible')
   # Simulate the auth-state event produced by a successful login, using only
   # this synthetic transport. Recovery must reload without a manual refresh.
   denial[0]=None
   page.evaluate("applyAuth({connected:true,id:7,username:'FixtureAccount',socialWrite:true})")
   page.locator('#communityMessage').filter(has_text='No recommendations match').wait_for()
   assert page.locator('#communityRecommend').is_enabled()
   page.locator('#tabGallery').click()
   enabled[0]=False;card_recommend()
   page.locator('#toast').filter(has_text='read-only').wait_for()
   assert not page.locator('dialog[open]').count()
   denial[0]='INVITE_ONLY';card_recommend()
   page.locator('#toast').filter(has_text='Invite Only').wait_for()
   assert not page.locator('dialog[open]').count() and not writes and not errors,(errors,writes)
   page.screenshot(path=str(test_safety.REPORTS/f'card-admission-{width}.png'))
   page.close()
  browser.close()
 print('PASS: desktop/mobile Daily-first card action, fresh admission, modal prefill, expired-auth feedback, login recovery, disabled/invitation denials; synthetic HTTP, zero recommendation writes')
finally:server.shutdown();server.server_close()

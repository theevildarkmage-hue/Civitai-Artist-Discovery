"""Actual UI, synthetic identity/policy HTTP; no hosted authentication or writes."""
import test_safety
import json,socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
h=Harness(port=port,sample=False)
checks=0
try:
 h.start()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  for width in (1440,390):
   for mode in ('invite_only_invited','invite_only_uninvited','public','public_read_only'):
    page=browser.new_page(viewport={'width':width,'height':900},extra_http_headers=h.headers())
    signed=[False];calls=[]
    def reply(route,value,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
    page.route('**/api/auth-status',lambda r:reply(r,{'connected':signed[0],'cachedSession':signed[0],'id':42,'username':'SyntheticMember'}))
    page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
    page.route('**/api/community/beta',lambda r:reply(r,{'production':True,'available':True,'enabled':True,'serverPolicy':True}))
    def status(route):
     calls.append('status')
     if mode=='invite_only_uninvited':return reply(route,{'code':'INVITE_ONLY','error':'Community Beta is Invite Only'},403)
     reply(route,{'apiVersion':1,'member':{'id':42},'quota':{'remaining':3,'used':0,'limit':3},'beta':{'available':True,'submissions':mode!='public_read_only','accessMode':'public' if mode.startswith('public') else 'invite_only'}})
    page.route('**/api/community/status*',status)
    def gallery(route):calls.append('gallery');reply(route,{'items':[],'nextCursor':None})
    page.route('**/api/community/gallery*',gallery)
    page.goto(h.base,wait_until='domcontentloaded');page.locator('#welcome:not(.hidden)').wait_for()
    signed[0]=True;page.reload(wait_until='domcontentloaded')
    page.wait_for_function('()=>document.querySelector("#tabCommunity")?.getAttribute("aria-pressed")==="true"')
    assert page.locator('.view-tabs > button').first.get_attribute('id')=='tabCommunity'
    if mode=='invite_only_uninvited':
     page.locator('#communityMessage').filter(has_text='Invite Only').wait_for()
     assert page.locator('#communityRecommend').is_disabled()
    else:
     page.locator('#communityQuota').filter(has_text='3 left').wait_for()
     assert 'gallery' in calls
     assert page.locator('#communityRecommend').is_disabled()==(mode=='public_read_only')
    assert page.get_by_text('Opt in to Community Beta',exact=False).count()==0
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('()=>document.querySelector("#tabCommunity")?.getAttribute("aria-pressed")==="true"')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
    page.screenshot(path=str(test_safety.REPORTS/f'community-{width}-{mode}.png'))
    checks+=1;page.close()
  browser.close()
 print(json.dumps({'passed':checks,'desktopAndMobile':True,'identityAndPolicySimulated':True,'recommendationWrites':0}))
finally:h.stop()

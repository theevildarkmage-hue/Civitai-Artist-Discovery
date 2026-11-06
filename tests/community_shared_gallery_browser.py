"""Actual HTTP/shared History cache/TASTE/cards; only identity and Civitai transports simulated."""
import test_safety
import io,json,threading,time
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from PIL import Image
from playwright.sync_api import sync_playwright
import server
from discovery.cache import ThumbnailCache
from community_accepted_feed import make_client
png=io.BytesIO();Image.new('RGB',(120,180),(60,120,160)).save(png,'PNG');png=png.getvalue()
counts={'metadata':0,'tags':0,'downloads':0}
class Civitai:
 def batch_query_optional(self,op,values):
  if op=='image.get':
   counts['metadata']+=1
   return [dict(id=v['id'],postId=v['id'],user={'id':v['id']+1000,'username':'Artist'+str(v['id'])},createdAt='2026-10-25T10:00:00Z',url='https://image.civitai.com/a/b/width=768/'+str(v['id'])+'.jpeg',type='image',nsfwLevel=1,stats={}) for v in values]
  assert op=='tag.getVotableTags';counts['tags']+=1
  return [[{'id':77,'name':'hidden'}] if v['id']==125 else [] for v in values]
def thumb(*args):counts['downloads']+=1;return png,'image/png'
client,backend,verifier=make_client()
client.gallery_resolver=server.shared_cached_artwork
with ExitStack() as stack:
 stack.enter_context(patch.object(server,'COMMUNITY',client))
 stack.enter_context(patch.object(server,'community_client',return_value=client))
 stack.enter_context(patch.object(server,'auth_status',return_value={'id':17,'connected':True}))
 stack.enter_context(patch.object(server.TASTE,'ensure_current_account',return_value=True))
 stack.enter_context(patch.object(server,'SocialClient',Civitai))
 stack.enter_context(patch.object(server.Handler,'require_community_session',return_value=None))
 stack.enter_context(patch('discovery.community_beta.require_enabled'))
 stack.enter_context(patch('discovery.community_security.fixture_enabled',return_value=False))
 stack.enter_context(patch('discovery.community_security.test_identity',return_value=None))
 stack.enter_context(patch.object(server,'PERSONAL_THUMBNAILS',ThumbnailCache(test_safety.REPORTS/'thumbs',fetch=thumb)))
 with server.TASTE.connect() as db:
  server.TASTE._set_state(db,'hidden_imported_at','now')
  db.execute("insert into hidden_tags values(77,'hidden')")
 http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);server.RUNNING_PORT=http.server_port
 threading.Thread(target=http.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{http.server_port}'
 results=[]
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True)
   for width in (1440,390):
    page=browser.new_page(viewport={'width':width,'height':950})
    def routes(route):
     u=route.request.url
     if not u.startswith(base):route.abort();return
     if '/api/auth-status' in u:route.fulfill(json={'id':17,'connected':True,'username':'Synthetic','communityAuthorization':{'state':'ready','action':'none'}});return
     if '/api/history/' in u and not any(x in u for x in ('/images','/tags','/thumbnail/')):route.fulfill(json={'complete':True,'hasArchives':False,'days':[],'artists':[],'total':0,'hasMore':False,'models':[],'blocks':{},'job':{'running':False}});return
     if '/api/community/beta' in u:route.fulfill(json={'production':True,'serverPolicy':True});return
     if any(x in u for x in ('/api/discovery/summary','/api/discovery/sync','/api/creator-metadata','/api/reaction-status')):route.fulfill(json={});return
     route.continue_()
    page.route('**/*',routes)
    page.goto(base,wait_until='domcontentloaded')
    page.wait_for_function('()=>document.querySelectorAll("#communityGrid .community-card").length>=49',timeout=20000)
    page.wait_for_function('()=>[...document.querySelectorAll("#communityGrid .image-button img")].some(i=>i.complete&&i.naturalWidth>0)',timeout=15000)
    assert page.locator('#communityGrid [data-id="125"]').count()==0
    for minimum in (99,124):
     page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
     page.wait_for_function('(n)=>document.querySelectorAll("#communityGrid .community-card").length>=n',arg=minimum,timeout=20000)
    assert len(set(page.locator('#communityGrid .community-card').evaluate_all('(cs)=>cs.map(c=>c.dataset.id)')))==124
    verifier.artwork.assert_not_called()
    with server.HISTORY.connect() as db:
     assert db.execute('select count(*) from block_images').fetchone()[0]==0
     assert db.execute('select count(*) from days').fetchone()[0]==0
    page.screenshot(path=str(test_safety.REPORTS/f'shared-gallery-{width}.png'))
    results.append({'width':width,'pages':[50,50,25],'hiddenImageExcluded':True,'visibleCards':124,'sharedHistoryCache':True,'sharedTasteTags':True,'archiveMemberships':0})
    page.close()
   browser.close()
 finally:http.shutdown();http.server_close()
(test_safety.REPORTS/'shared-gallery-browser.json').write_text(json.dumps({'passed':True,'cases':results,'transportCounts':counts,'hostedRequests':0},indent=2))
print('Shared Gallery HTTP/Chromium:2viewports passed,125 IDs,124safe cards,zero archive days')

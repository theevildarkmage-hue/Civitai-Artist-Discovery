"""One isolated desktop origin/profile for a simulated public creator identity."""
import sys
if getattr(sys,'frozen',False):raise RuntimeError('Community Test Lab is excluded from packaged applications')
import json,os,threading,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from http.server import ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[2]
if any(k.startswith(('PG','SUPABASE_','CAD_TEST_','CAD_ALLOW_')) for k in os.environ):
    raise RuntimeError('Privileged service/test environment must not reach an isolated desktop')
config_path=Path(sys.argv[1]).resolve();config=json.loads(config_path.read_text())
data=config_path.parent
if not data.name.startswith('data-test-lab-member-') or ROOT in data.parents:
    raise RuntimeError('An external isolated lab profile is required')
if any((data/name).exists() for name in ('oauth_tokens.dpapi','oauth_client.json')):
    raise RuntimeError('Lab profiles must never contain real OAuth storage')
for target in ('manager','edge'):
    url=urlsplit(config[target]);assert url.scheme=='http' and url.hostname=='127.0.0.1' and url.port
os.environ.update(CIVITAI_HISTORY_DATA_DIR=str(data),CAD_DEVELOPMENT='1',CAD_COMMUNITY_MODE='supabase',
    CAD_COMMUNITY_TESTING='0',CAD_COMMUNITY_URL='https://local-test.supabase.co/functions/v1/community-v1')
sys.path.insert(0,str(ROOT))
import server
from discovery.community import CommunityClient,CivitaiVerifier,SupabaseCommunityBackend,ContentProfile,CommunityError
from discovery.cache import MetadataCache,ArtworkCache,ThumbnailCache
from lab import fixtures

def manager():
    req=urllib.request.Request(config['manager']+'/session',data=json.dumps({'index':config['index']}).encode(),
        headers={'Content-Type':'application/json','X-Lab-Capability':config['capability']})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
def access():return manager()['token']
catalog=fixtures();artworks={a['rest']['id']:a for m in catalog['members'] for a in m['artworks']}
def metadata(url,token=None,body=None):
    parsed=urlsplit(url)
    if parsed.hostname!='civitai.red':raise CommunityError('UNAVAILABLE',503)
    if parsed.path=='/api/v1/images':
        image=int(parse_qs(parsed.query)['imageId'][0]);art=artworks.get(image)
        return {'items':[art['rest']] if art else []}
    if parsed.path=='/api/trpc/image.get':
        image=json.loads(parse_qs(parsed.query)['input'][0])['json']['id'];art=artworks.get(image)
        if not art:raise CommunityError('INVALID_ARTWORK',404)
        return {'result':{'data':{'json':art['detail']}}}
    if parsed.path=='/api/trpc/tag.getVotableTags':
        image=json.loads(parse_qs(parsed.query)['input'][0])['json']['id'];art=artworks.get(image)
        if not art or 'tags' not in art:raise CommunityError('INVALID_ARTWORK',404)
        return {'result':{'data':{'json':art['tags']}}}
    raise CommunityError('FORBIDDEN',403)
def transport(url,token,payload):
    req=urllib.request.Request(config['edge'],data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+access()})
    try:
        with urllib.request.urlopen(req,timeout=12) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        value=json.load(error);raise CommunityError(value.get('code','UNAVAILABLE'),error.code)
    except OSError:raise CommunityError('UNAVAILABLE',503)
identity=config['creator']
verifier=CivitaiVerifier(fetch=metadata,token_provider=access)
client=CommunityClient(SupabaseCommunityBackend(os.environ['CAD_COMMUNITY_URL'],fetch=transport),verifier,access,
    lambda token:ContentProfile(levels=frozenset(server.SETTINGS.load()['browsingLevels'])))
client.mode='supabase';client.identity_provider=lambda:identity['id']
client.store=MetadataCache(data/'cache/community-metadata.sqlite3')
client.artwork_cache=ArtworkCache(client.store,verifier,'local-lab')
client.thumbnails=ThumbnailCache(data/'cache/thumbnails',allow_downscale=True)
server.COMMUNITY=client
server.auth_status=lambda:{'connected':True,'id':identity['id'],'username':identity['username']+' (Simulated member)',
    'socialWrite':False,'userWrite':False,'collectionsWrite':False,'collectionsRead':False,'cachedSession':False,
    'sessionState':'local-simulated','developmentIdentity':True}
server.get_access_token=access
# Only this isolated child registers the local session boundary. The normal
# Handler retains its OAuth implementation and cannot select this identity.
def require_local_session(handler):
    try:
        if not access():raise ValueError()
    except Exception:raise CommunityError('AUTH_REQUIRED',401) from None
server.Handler.require_community_session=require_local_session
old_get=server.Handler.do_GET
old_post=server.Handler.do_POST

def post(handler):
    # Isolated actors cannot start real OAuth, capture or Civitai social writes.
    if urlsplit(handler.path).path not in ('/api/community/recommend','/api/community/withdraw','/api/settings','/api/app/close'):
        handler.json_response({'error':'Unavailable in a simulated local member session.'},403);return
    old_post(handler)
server.Handler.do_POST=post
def get(handler):
    if not handler.host_allowed():handler.send_error(403);return
    path=urlsplit(handler.path).path
    if path=='/api/auth-status':handler.json_response(server.auth_status());return
    if path=='/api/discovery/summary':handler.json_response({'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'});return
    if path=='/api/reaction-status':
        ids=parse_qs(urlsplit(handler.path).query).get('imageId',[])[:100]
        handler.json_response({'images':{value:{'reactions':[],'stats':artworks[int(value)]['rest'].get('stats',{})}
            for value in ids if value.isdecimal() and int(value) in artworks}});return
    if path=='/api/creator-metadata':
        handler.json_response({'creators':{m['creator']['username'].casefold():{'username':m['creator']['username'],
            'userId':m['creator']['id'],'avatarUrl':m['creator']['avatar'],'following':False} for m in catalog['members']}});return
    old_get(handler)
server.Handler.do_GET=get
if not (data/'settings.json').exists():(data/'settings.json').write_text(json.dumps({'browsingLevels':[1,2,4,8,16],'autoCapture':False,'checkForUpdates':False}))
http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);server.RUNNING_PORT=http.server_port
(data/'session-url.json').write_text(json.dumps({'url':f'http://127.0.0.1:{http.server_port}'}))
try:http.serve_forever()
finally:http.server_close()

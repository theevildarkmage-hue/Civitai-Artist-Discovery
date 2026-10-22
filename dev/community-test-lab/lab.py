"""Removable local lab: real public metadata, simulated issuer, actual service/SQL.

Imported only by owned development/test launchers, never production application code.
"""
from pathlib import Path
import concurrent.futures
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def fixtures():
    value=json.loads((HERE/'public-fixtures.json').read_text(encoding='utf-8'))
    assert len(value['members'])==10 and value['developmentOnly']
    for member in value['members']:
        assert len(member['artworks'])>=4
        for art in member['artworks']:
            assert art['detail']['user']['id']==member['creator']['id']
            assert art['detail']['user']['username']==art['rest']['username']
    return value


class Lab:
    def __init__(self,edge,mint,sql,scratch,output,rename=None):
        if getattr(sys,'frozen',False) or os.environ.get('CAD_ALLOW_LOCAL_HTTP_TESTS')!='1':
            raise RuntimeError('Lab is restricted to an explicitly owned unpackaged local service')
        self.members=fixtures()['members'];self.edge=edge;self.mint=mint;self.sql=sql
        self.scratch=Path(scratch);self.output=Path(output);self.key=secrets.token_urlsafe(32)
        self.scratch.mkdir(parents=True,exist_ok=True);self.rename=rename
        self.sessions={};self.processes=[];self.logs=[];self.session_processes={};self.stop_event=threading.Event()
    def call(self,index,operation,**fields):
        if type(index) is not int or not 0<=index<10:raise ValueError('Choose a known local simulated member')
        req=urllib.request.Request(self.edge,data=json.dumps({'apiVersion':1,'operation':operation,**fields}).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.mint(index)})
        try:
            with urllib.request.urlopen(req,timeout=15) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    def snapshot(self):
        result=[]
        for index,item in enumerate(self.members):
            status,value=self.call(index,'status')
            result.append({'index':index,'creator':item['creator'],'simulated':True,
                'quota':value.get('quota') if status==200 else None,'session':self.sessions.get(index),
                'artworks':[{'id':a['rest']['id'],'url':f"https://civitai.red/images/{a['rest']['id']}",
                    'thumbnail':a['rest']['url'],'rating':a['rest']['browsingLevel']} for a in item['artworks']]})
        return result
    def inspect(self,index,artwork):
        from discovery.community import parse_artwork
        image=parse_artwork(artwork)
        art=next((a for m in self.members for a in m['artworks'] if a['rest']['id']==image),None)
        status,value=self.call(index,'verify',artwork=image)
        return {'member':self.members[index]['creator'],'simulated':True,'imageId':image,
            'uploader':art['detail']['user'] if art else None,'method':'Verified issuer sub compared with Civitai image.get user.id in the actual SQL submission RPC',
            'httpStatus':status,'outcome':value.get('code','ELIGIBLE_OTHER_UPLOADER' if status==200 else 'INCONCLUSIVE'),
            'reason':value.get('error','Uploader differs; other submission constraints currently permit a recommendation.'),
            'metadataSource':'Bounded validated public Civitai snapshot; no claim of current live account authentication'}
    def open(self,index):
        if type(index) is not int or not 0<=index<10:raise ValueError('Choose a known local simulated member')
        if index in self.sessions and self.session_processes[index].poll() is None:return self.sessions[index]
        profile=self.scratch/f'data-test-lab-member-{index}';profile.mkdir(exist_ok=True)
        if (profile/'session-url.json').exists():(profile/'session-url.json').unlink()
        config=profile/'lab-session.json'
        config.write_text(json.dumps({'manager':self.base,'capability':self.key,'index':index,'edge':self.edge,
            'creator':self.members[index]['creator']}),encoding='utf-8')
        log=(profile/'desktop.log').open('w',encoding='utf-8');self.logs.append(log)
        actor_env={k:v for k,v in os.environ.items() if not k.startswith(('PG','SUPABASE_','CAD_TEST_','CAD_ALLOW_'))}
        actor_env.update(CIVITAI_HISTORY_DATA_DIR=str(profile),CAD_DEVELOPMENT='1')
        process=subprocess.Popen([sys.executable,'-u','-B',str(HERE/'actor.py'),str(config)],cwd=ROOT,
            env=actor_env,
            stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.processes.append(process)
        self.session_processes[index]=process
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError('Isolated member desktop failed; inspect its private log')
            if (profile/'session-url.json').exists():
                url=json.loads((profile/'session-url.json').read_text())['url'];self.sessions[index]=url;return url
            time.sleep(.1)
        raise RuntimeError('Member desktop startup timed out')
    def start(self):
        lab=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.dispatch()
            def do_POST(self):self.dispatch()
            def dispatch(self):
                if self.client_address[0]!='127.0.0.1' or not secrets.compare_digest(self.headers.get('X-Lab-Capability',''),lab.key):
                    self.send_error(403);return
                try:
                    body=json.loads(self.rfile.read(min(int(self.headers.get('Content-Length','0')),8193))) if self.command=='POST' else {}
                    if self.path=='/status':value=lab.snapshot()
                    elif self.path=='/inspect':value=lab.inspect(int(body['index']),body['artwork'])
                    elif self.path=='/open':value={'url':lab.open(int(body['index']))}
                    elif self.path=='/session':
                        index=int(body['index']);assert 0<=index<10
                        value={'token':lab.mint(index),'fixtures':fixtures()}
                    elif self.path=='/shutdown':lab.stop_event.set();value={'stopping':True}
                    else:self.send_error(404);return
                    raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except Exception:self.send_error(400,'Invalid local lab request')
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.base=f'http://127.0.0.1:{self.http.server_port}'
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.state=self.scratch/'manual-lab-connection.json'
        self.state.write_text(json.dumps({'manager':self.base,'capability':self.key}),encoding='utf-8')
        (self.output/'lab-location.json').write_text(json.dumps({'connectionFile':str(self.state),'credentialsInThisReport':False}),encoding='utf-8')
        return self
    def close(self):
        if getattr(self,'closed',False):return
        self.closed=True
        for process in self.processes:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
        for log in self.logs:log.close()
        self.http.shutdown();self.http.server_close()
        if self.state.exists():self.state.unlink()


def exercise(lab):
    """Real fixture IDs through actual Deno/PostgREST/SQL, never fixture SQLite writes."""
    members=lab.members;ids=[a['rest']['id'] for m in members for a in m['artworks']]
    memberids=[m['creator']['id'] for m in members]
    def clean():
        images=','.join(map(str,ids));actors=','.join(map(str,memberids))
        lab.sql(f'begin; delete from community_submission_requests where image_id in ({images}); delete from community_audit where image_id in ({images}); delete from community_recommendations where image_id in ({images}); delete from community_members where civitai_id in ({actors}); delete from community_rate_buckets; commit;')
    def state():return json.loads(lab.sql('select json_build_object(\'recommendations\',(select count(*) from community_recommendations where image_id in ('+','.join(map(str,ids))+')),\'audit\',(select count(*) from community_audit where image_id in ('+','.join(map(str,ids))+')));'))
    def image(m,n=0):return members[m]['artworks'][n]['rest']['id']
    results=[]
    try:
        for round in range(3):
            clean()
            for index in range(10):
                before=state();status,value=lab.call(index,'recommend',artwork=image(index));assert status==403 and value['code']=='SELF_RECOMMENDATION';assert state()==before
            assert lab.call(0,'recommend',artwork=image(1))[0]==200
            assert lab.call(1,'recommend',artwork=image(0))[0]==200
            before=state();status,value=lab.call(0,'recommend',artwork=image(0,1),creatorId=members[1]['creator']['id']);assert status==400 and value['code']=='INVALID_REQUEST';assert state()==before
            # Change the trusted local UserInfo display name, not the real account.
            if lab.rename:
                original=members[0]['creator']['username']
                try:
                    lab.rename(0,original+'-renamed-locally')
                    assert lab.call(0,'status')[1]['member']['username']==original+'-renamed-locally'
                    assert lab.call(0,'verify',artwork=image(0,1))[1]['code']=='SELF_RECOMMENDATION'
                finally:lab.rename(0,original)
            assert lab.call(0,'verify',artwork=99999999999999)[0] in (400,404)
            for uploader,member in ((2,3),(4,3),(5,3)):assert lab.call(member,'recommend',artwork=image(uploader))[0]==200
            assert lab.call(3,'recommend',artwork=image(6))[1]['code']=='MEMBER_QUOTA'
            for actor,n in ((4,0),(5,1),(6,2)):assert lab.call(actor,'recommend',artwork=image(7,n))[0]==200
            assert lab.call(8,'recommend',artwork=image(7,3))[1]['code']=='ARTIST_QUOTA'
            races=[]
            def race(name,requests,accepted,replayed=0,code=None):
                before=state();barrier=threading.Barrier(len(requests))
                def compete(request):
                    actor,art,key=request;barrier.wait(timeout=10)
                    return lab.call(actor,'recommend',artwork=art,**({'idempotencyKey':key} if key else {}))
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(requests)) as pool:responses=list(pool.map(compete,requests))
                assert sum(s==200 for s,_ in responses)==accepted,(name,responses)
                assert sum(bool(v.get('replayed')) for _,v in responses)==replayed
                assert all(s==200 or v.get('code')==code for s,v in responses),(name,responses)
                after=state();assert after['recommendations']-before['recommendations']==accepted-replayed
                assert after['audit']-before['audit']==accepted-replayed
                races.append({'scenario':name,'sent':len(requests),'accepted':accepted,'rejected':len(requests)-accepted,
                    'replayed':replayed,'persisted':accepted-replayed,'before':before,'after':after})
            clean()
            for owner in (2,4):assert lab.call(3,'recommend',artwork=image(owner))[0]==200
            race('last-member-slot',[(3,image(owner),None) for owner in (5,6)],1,code='MEMBER_QUOTA')
            clean()
            for actor,n in ((4,0),(5,1)):assert lab.call(actor,'recommend',artwork=image(7,n))[0]==200
            race('last-artist-slot',[(actor,image(7,n),None) for actor,n in ((6,2),(8,3))],1,code='ARTIST_QUOTA')
            clean();race('duplicate',[(n,image(9),None) for n in range(6)],1,code='DUPLICATE')
            clean();race('same-key-replay',[(0,image(9),f'public-fixture-replay-{round}')]*6,6,replayed=5)
            results.append({'round':round+1,'realMetadata':True,'selfRejections':10,'reciprocalAccepted':2,
                'stableIdAfterLocalRename':bool(lab.rename),'forgedFieldsRejectedBeforeWrite':True,
                'unknownArtworkRejected':True,'memberQuotaRejected':True,'artistQuotaRejected':True,'races':races})
        clean()
        return {'passed':True,'creators':10,'validatedArtworks':len(ids),'rounds':results,
            'simulatedAuthentication':True,'realPostgreSQL':True,'realHTTP':True,'snapshotMetadata':True}
    finally:clean()


def exercise_browser(lab):
    """No Community response mocking: two real isolated desktops and public thumbnails."""
    from playwright.sync_api import sync_playwright
    import sqlite3
    first,second=lab.open(0),lab.open(1)
    members=lab.members;image=lambda m,n=0:members[m]['artworks'][n]['rest']['id']
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        contexts=[browser.new_context(viewport={'width':1440,'height':1000}) for _ in range(2)]
        pages=[context.new_page() for context in contexts]
        for page,url in zip(pages,(first,second)):
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url,wait_until='domcontentloaded');page.locator('#tabCommunity').click()
            page.locator('#communityQuota').filter(has_text='3 left').wait_for()
        pages[0].evaluate("localStorage.setItem('labIsolation','A')")
        assert pages[1].evaluate("localStorage.getItem('labIsolation')") is None
        def submit(page,value):
            page.locator('#communityRecommend').click();page.locator('#communityLink').fill(str(value));page.locator('#communitySubmit').click()
        submit(pages[0],image(0));pages[0].locator('#communityDialogMessage').filter(has_text='own artwork').wait_for()
        pages[0].screenshot(path=str(lab.output/'real-lab-self-rejected.png'));pages[0].keyboard.press('Escape')
        submit(pages[0],image(1));pages[0].locator('#communityQuota').filter(has_text='2 left').wait_for()
        submit(pages[1],image(0));pages[1].locator('#communityQuota').filter(has_text='2 left').wait_for()
        # Balanced assignment of 28 more real artworks without inventing metadata.
        # Integer max flow fills member/artist capacities while forbidding self edges.
        capacity={};flow={};source=-1;sink=20
        def edge(a,b,n):capacity[a,b]=n;capacity.setdefault((b,a),0);flow[a,b]=flow[b,a]=0
        for i in range(10):edge(source,i,2 if i<2 else 3);edge(10+i,sink,2 if i<2 else 3)
        for i in range(10):
            for j in range(10):
                if i!=j:edge(i,10+j,3)
        while True:
            previous={source:None};queue=[source]
            for a in queue:
                for x,b in capacity:
                    if x==a and b not in previous and capacity[a,b]-flow[a,b]>0:previous[b]=a;queue.append(b)
                if sink in previous:break
            if sink not in previous:break
            path=[];b=sink
            while previous[b] is not None:a=previous[b];path.append((a,b));b=a
            amount=min(capacity[a,b]-flow[a,b] for a,b in path)
            for a,b in path:flow[a,b]+=amount;flow[b,a]-=amount
        assert sum(flow[source,i] for i in range(10))==28
        positions=[1 if i<2 else 0 for i in range(10)]
        for i in range(10):
            for j in range(10):
                for _ in range(flow.get((i,10+j),0)):
                    status,value=lab.call(i,'recommend',artwork=image(j,positions[j]));assert status==200,(status,value)
                    positions[j]+=1
        page=pages[0]
        page.locator('#communityRefresh').click();page.wait_for_timeout(1200);page.locator('#communityRefresh').click()
        page.locator('#communityGrid .creator-card').first.wait_for(timeout=30000)
        deadline=time.monotonic()+30
        while 'caught up' not in page.locator('#communitySentinel').inner_text().lower():
            assert time.monotonic()<deadline
            page.locator('#communitySentinel').scroll_into_view_if_needed();page.mouse.wheel(0,600);page.wait_for_timeout(200)
        page.wait_for_function("()=>document.querySelectorAll('#communityGrid .image-progress button').length===30",timeout=30000)
        page.wait_for_function("()=>[...document.querySelectorAll('#communityGrid .image-button img')].some(x=>x.naturalWidth>0)",timeout=30000)
        page.locator('#communityGrid .creator-card').first.scroll_into_view_if_needed()
        page.wait_for_function("()=>document.querySelector('#communityGrid .image-button img')?.naturalWidth>0",timeout=30000)
        page.wait_for_function("()=>[...document.querySelectorAll('#communityGrid img.creator-avatar')].some(x=>x.naturalWidth>0)",timeout=30000)
        page.screenshot(path=str(lab.output/'real-lab-gallery-desktop.png'))
        page.locator('#communityGrid .info-button').first.click();page.locator('#details[open]').wait_for();page.locator('#close').click()
        page.set_viewport_size({'width':390,'height':844});page.locator('#communityGrid .creator-card').first.scroll_into_view_if_needed()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        page.screenshot(path=str(lab.output/'real-lab-gallery-mobile.png'))
        # Apply the existing shared controls to real ratings, in this isolated profile.
        page.locator('#contentFilter').click()
        for level in (2,4,8,16):page.locator(f'#contentPanel [data-level="{level}"]').click()
        page.keyboard.press('Escape')
        allowed=[a['rest']['id'] for m in members for a in m['artworks'] if a['rest']['browsingLevel']==1]
        page.wait_for_function("ids=>{const cards=[...document.querySelectorAll('#communityGrid .creator-card')];return cards.length>0&&cards.every(x=>ids.includes(Number(x.dataset.id)));}",arg=allowed,timeout=30000)
        for page in pages:
            page.locator('#tabGallery').click();page.locator('#tabCommunity').click()
            try:page.locator('#communityGrid .creator-card').first.wait_for()
            except Exception:
                (lab.output/'navigation-failure.json').write_text(json.dumps(page.evaluate("()=>({quota:document.querySelector('#communityQuota').textContent,message:document.querySelector('#communityMessage').textContent,communityHidden:document.querySelector('#community').classList.contains('hidden'),cards:document.querySelector('#communityGrid').children.length,sentinel:document.querySelector('#communitySentinel').textContent})")),encoding='utf-8')
                page.screenshot(path=str(lab.output/'navigation-failure.png'));raise
        assert not errors,errors
        browser.close()
    for index in (0,1):
        data=lab.scratch/f'data-test-lab-member-{index}'
        assert not (data/'oauth_tokens.dpapi').exists() and not (data/'oauth_client.json').exists()
    # Leave the manual lab fresh, not quota-exhausted after browser acceptance.
    ids=','.join(str(a['rest']['id']) for m in members for a in m['artworks'])
    actors=','.join(str(m['creator']['id']) for m in members)
    lab.sql(f'begin; delete from community_submission_requests where image_id in ({ids}); delete from community_audit where image_id in ({ids}); delete from community_recommendations where image_id in ({ids}); delete from community_members where civitai_id in ({actors}); delete from community_rate_buckets; commit;')
    return {'passed':True,'memberOrigins':[first,second],'separateBrowserContexts':True,'separateStorage':True,
        'selfRejectedInActualUI':True,'reciprocalAcceptedInActualUI':True,'publicArtworkDecoded':True,
        'realPostgreSQLFeedArtworks':30,'automaticPagination':True,'publicAvatarDecoded':True,
        'realRatingControls':True,'galleryNavigation':True,'noRealOAuthFiles':True,'browserErrors':errors}

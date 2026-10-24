"""Shared browser journey helper; invoked only inside the owned HTTP cluster run."""
def run_gallery(edge,member,seed,creators,sql,scratch,output,get_service,start_service):
    import json,os,secrets,sys,threading,time,urllib.request,urllib.error,subprocess
    from pathlib import Path
    from http.server import ThreadingHTTPServer
    from playwright.sync_api import sync_playwright
    root=Path(__file__).resolve().parents[1]
    data=scratch/'data-test-http-gallery';data.mkdir()
    nonce=secrets.token_urlsafe(32)
    os.environ.update(CIVITAI_HISTORY_DATA_DIR=str(data),CAD_COMMUNITY_MODE='fixture',
        CAD_COMMUNITY_TESTING='1',CAD_DEVELOPMENT='1',CAD_TEST_INSTANCE=nonce)
    (data/'settings.json').write_text(json.dumps({'browsingLevels':[1,2],'autoCapture':False,'checkForUpdates':False}))
    sys.path.insert(0,str(root))
    import server
    from discovery.community import build_community,Artwork,SupabaseCommunityBackend,CommunityError
    client=build_community(data,server.community_content_profile,lambda:'fixture:1')
    rows=json.loads(sql('select coalesce(json_agg(json_build_object(\'image\',image_id,\'creator\',creator_id)),\'[]\') from community_recommendations;'))
    first=seed+60000
    if first%2==0:first+=1
    new_images=[first,first+2,first+4]
    for image in new_images:rows.append({'image':image,'creator':creators.get(image,image+100000)})
    for row in rows:
        image,creator=row['image'],row['creator']
        client.verifier.artworks[image]=Artwork(image,creator,f'Artist{creator}',1 if image%2 else 2,
            f'/community-fixture.svg?id={image}')
    expired=False
    def transport(url,token,payload):
        # Legacy HTTP business-rule bridge: the desktop supplies only a synthetic
        # Discovery session. This local adapter substitutes the legacy issuer token;
        # it does not exercise the new identity exchange (covered separately).
        assert token == 'sdc_'+'t'*43
        request=urllib.request.Request(edge,data=json.dumps(payload).encode(),headers={
            'Content-Type':'application/json','Authorization':'Bearer '+member(70,expired=expired)})
        try:
            with urllib.request.urlopen(request,timeout=12) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            value=json.load(error);raise CommunityError(value.get('code','UNAVAILABLE'),error.code)
        except OSError:raise CommunityError('UNAVAILABLE',503)
    client.backend=SupabaseCommunityBackend('https://local-test.supabase.co/functions/v1/community-v1',fetch=transport,identity_token_provider=lambda:'sdc_'+'t'*43)
    server.COMMUNITY=client
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);server.RUNNING_PORT=http.server_port
    base=f'http://127.0.0.1:{http.server_port}'
    threading.Thread(target=http.serve_forever,daemon=True).start()
    errors=[];pages=[];old_service=get_service()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':1000},extra_http_headers={'X-CAD-Test-Instance':nonce})
            def route_guard(route):
                if route.request.url.startswith(base):route.continue_()
                else:route.abort()
            context.route('**/*',route_guard)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            def reply(route,value):route.fulfill(content_type='application/json',body=json.dumps(value))
            page.route('**/api/auth-status',lambda route:reply(route,{'connected':True,'id':seed+70,'username':'SyntheticHTTPMember','socialWrite':False}))
            page.route('**/api/discovery/summary',lambda route:reply(route,{'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
            page.route('**/api/creator-metadata?*',lambda route:reply(route,{'creators':{}}))
            page.route('**/api/reaction-status?*',lambda route:reply(route,{'images':{}}))
            page.on('request',lambda request:pages.append(request.url) if '/api/community/gallery?' in request.url else None)
            page.goto(base,wait_until='networkidle');page.locator('#tabCommunity').click()
            page.locator('#communityGrid .creator-card').first.wait_for()
            deadline=time.monotonic()+20
            while 'caught up' not in page.locator('#communitySentinel').inner_text().lower():
                assert time.monotonic()<deadline
                page.locator('#communitySentinel').scroll_into_view_if_needed();page.mouse.wheel(0,600);page.wait_for_timeout(100)
            assert len(pages)>=3
            names=page.locator('#communityGrid .creator-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.username)')
            assert len(names)==len(set(names))
            assert page.locator('#communityGrid .community-credit').count()==len(names)
            page.screenshot(path=str(output/'gallery-desktop.png'))
            page.locator('#communityGrid .info-button').first.click();page.locator('#details[open]').wait_for();page.locator('#close').click()
            # Existing controls API and card factory; Community endpoints are never mocked.
            page.locator('#filterToggle').click();page.locator('#contentMenu [data-level="2"]').click();page.keyboard.press('Escape')
            page.wait_for_timeout(400)
            page.wait_for_function("()=>[...document.querySelectorAll('#communityGrid .creator-card')].every(x=>Number(x.dataset.id)%2===1)")
            page.locator('#communityRecommend').click();page.locator('#communityLink').fill(str(new_images[0]));page.locator('#communitySubmit').click()
            page.locator('#communityQuota').filter(has_text='2 left').wait_for()
            # Probe expiration through actual HTTP without racing the asynchronous
            # post-submit status refresh that may correctly close the UI dialog.
            expired=True
            before=sql(f'select count(*) from community_recommendations where member_id={seed+70};')
            try:
                client.backend.request('recommend','fixture:1',{'artwork':new_images[1]})
                raise AssertionError('Expired identity accepted')
            except CommunityError as error:assert error.code=='AUTH_EXPIRED'
            assert sql(f'select count(*) from community_recommendations where member_id={seed+70};')==before
            expired=False;page.locator('#communityRefresh').click()
            page.wait_for_function('()=>!document.querySelector("#communityRecommend").disabled')
            page.wait_for_load_state('networkidle')
            page.locator('#communityRecommend').click();page.locator('#communityLink').fill(str(new_images[1]));page.locator('#communitySubmit').click()
            page.locator('#communityQuota').filter(has_text='1 left').wait_for()
            old_service.terminate();old_service.wait(timeout=10)
            # Cached browse remains available; fresh writes fail without partial data.
            page.locator('#communityRefresh').click();page.wait_for_timeout(300)
            page.locator('#communityGrid .creator-card').first.wait_for(timeout=30000)
            new_service=start_service()
            for _ in range(50):
                try:
                    client.backend.request('status','fixture:1',{});break
                except CommunityError:time.sleep(.1)
            else:raise AssertionError('Restarted service did not recover')
            page.locator('#communityRefresh').click();page.wait_for_timeout(300)
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.screenshot(path=str(output/'gallery-mobile.png'))
            assert not errors,errors;browser.close()
        # No recommendation was accepted into the desktop's simulator SQLite ledger.
        import sqlite3
        with sqlite3.connect(data/'community/fixtures.sqlite3') as db:
            assert db.execute('select count(*) from recommendations').fetchone()[0]==0
        return {'passed':True,'automaticPageRequests':len(pages),'artistCards':len(names),
            'syntheticMetadataAndDesktopIdentity':True,'communityHttpResponsesMocked':False,
            'expiredWriteRejected':True,'quotaUpdates':True,'serviceRestart':True,'offlineCachedBrowse':True,
            'browserErrors':errors,'simulatorAcceptedRows':0}
    finally:http.shutdown();http.server_close()

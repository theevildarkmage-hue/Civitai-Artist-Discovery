"""Read-only real-session UI smoke plus mutations solely in disposable test backends."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8907'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=15) as response:return json.load(response)


def critical_data(data):
    result={}
    for name,tables in {'community/fixtures.sqlite3':('recommendations','audit','submission_requests'),
        'community/local.sqlite3':('recommendations','audit','submission_requests'),
        'discovery/taste.sqlite3':('reacted_images','reacted_reactions','followed_creators','archive_image_tags','archive_image_seen'),
        'history/history.sqlite3':('images','block_images','metadata_imports','metadata_import_runs')}.items():
        path=data/name
        if not path.exists():continue
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10)
        try:
            db.execute('PRAGMA query_only=ON')
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            available={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in tables:
                if table not in available:continue
                encoded=sorted(json.dumps(row,default=str) for row in db.execute('SELECT * FROM '+table))
                result[name+':'+table]={'count':len(encoded),'sha256':hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
        finally:db.close()
    return result


def check_instance():
    info=get('/api/development/diagnostics')
    if (not info.get('development') or Path(info['sourceDirectory']).resolve()!=ROOT or
            info.get('version')!='3.0.0-dev'):
        raise RuntimeError('The live endpoint is not this development checkout; no action performed.')
    record=json.loads((Path(info['dataDirectory'])/'running-instance.json').read_text())
    if record!={'url':BASE,'pid':info['processId']}:raise RuntimeError('Live instance identity does not match the pinned data record.')
    return info


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart',action='store_true',help='Gracefully restart only the verified development instance.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or Path(tempfile.mkdtemp(prefix='cad3-smoke-'))
    output.mkdir(parents=True,exist_ok=True)
    result={'liveChecks':{},'isolatedTests':{},'limitations':[
        'Live profile receives no submission, settings or sync mutations.',
        'Invalid/valid submissions, quotas, duplicates and offline outage behavior use isolated synthetic backends.',
        'No PostgreSQL/RLS/deployed Edge or live OAuth revocation validation.']}
    def save(): (output/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'Smoke artifacts: {output.resolve()}',flush=True)
    try:
        info=check_instance();data=Path(info['dataDirectory']).resolve()
        pin=ROOT/'.data-directory-3.0.json'
        selected=(ROOT/json.loads(pin.read_text())['directory']).resolve() if pin.exists() else ROOT/'data-dev-3.0'
        if data!=selected:raise RuntimeError('The endpoint is not the selected persistent profile.')
        before=critical_data(data)
        result['criticalDataBefore']=before;save()
        auth=get('/api/auth-status');assert auth.get('connected') and auth.get('cachedSession')
        result['liveChecks']['session']={'connected':True,'cachedSession':True}
        result['liveChecks']['instance']={'url':BASE,'pid':info['processId'],'gitCommit':info['gitCommit']}
        if args.restart:
            request=urllib.request.Request(BASE+'/api/app/restart',data=b'{}',headers={'Content-Type':'application/json','Origin':BASE})
            with urllib.request.urlopen(request,timeout=10) as response:assert json.load(response).get('restarting')
            deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                try:
                    after=check_instance()
                    if after['processId']!=info['processId']:break
                except (OSError,ValueError):pass
                time.sleep(.2)
            else:raise RuntimeError('The verified restart did not become ready; no fallback instance started.')
            auth=get('/api/auth-status');assert auth.get('connected') and auth.get('cachedSession')
            result['liveChecks']['restart']={'passed':True,'previousPid':info['processId'],'pid':after['processId']}
            info=after
        else:result['liveChecks']['restart']={'skipped':True,'reason':'Use --restart for the full smoke command.'}
        completed=info['dailyGallery']['latestCompleted'];assert completed,'No cached real Daily gallery available'
        day,_,segment=completed.partition('#');segment=segment or 'all'
        # Prefer a complete full-day snapshot over a legacy/quarantined half-day.
        if get(f'/api/history/status?date={day}&segment=all').get('complete'):segment='all'
        errors=[];blocked=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            def guard(route):
                if route.request.url.startswith(BASE) and route.request.method not in ('GET','HEAD'):
                    blocked.append(route.request.url.removeprefix(BASE));route.abort()
                else:route.continue_()
            context.route('**/*',guard)
            state=json.dumps({'date':day,'segment':segment,'view':'discovery','models':[],'loaded':24,'scrollY':0})
            context.add_init_script('sessionStorage.setItem("civitai-feed-state",'+json.dumps(state)+');')
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(BASE,wait_until='domcontentloaded')
            page.wait_for_function("()=>typeof window.selectGalleryDate==='function'")
            page.locator('#tabGallery').click()
            page.evaluate('([day,segment])=>window.selectGalleryDate(day,segment)',[day,segment])
            page.wait_for_function('()=>[...document.querySelectorAll("#gallery .image-button img")].some(x=>x.naturalWidth>0)',timeout=30000)
            result['liveChecks']['daily']={'realArtworkDecoded':True,'summary':page.locator('#summary').inner_text()}
            page.screenshot(path=str(output/'daily-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.screenshot(path=str(output/'daily-mobile.png'))
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('#tabCommunity').click()
            page.wait_for_function('()=>document.querySelector("#communityQuota").textContent.includes("left")')
            page.wait_for_function('()=>[...document.querySelectorAll("#communityGrid img")].some(x=>x.naturalWidth>0)',timeout=30000)
            pages=1
            while page.locator('#communityMore').is_visible() and pages<10:
                with page.expect_response(lambda response:'/api/community/gallery?' in response.url):page.locator('#communityMore').click()
                page.wait_for_timeout(200);pages+=1
            page.screenshot(path=str(output/'community-desktop.png'))
            names=page.locator('#communityGrid .creator-card').evaluate_all('(xs)=>xs.map(x=>x.dataset.username)')
            assert len(names)==len(set(names)), 'Community displayed duplicate artist cards'
            assert page.locator('#communityGrid .follow-button').count()==len(names)
            assert page.locator('#communityGrid .reaction-bar').count()==len(names)
            result['liveChecks']['community']={'cards':page.locator('#communityGrid > *').count(),'pages':pages,
                'uniqueArtistCards':True,'sharedFollowAndReactionsPresent':True,
                'recommendedArtworks':page.locator('#communityGrid .image-progress button').count(),
                'decodedImages':page.locator('#communityGrid img').evaluate_all('(xs)=>xs.filter(x=>x.naturalWidth>0).length')}
            page.locator('#contentFilter').click()
            levels=page.locator('#contentPanel [aria-pressed="true"]').evaluate_all('(xs)=>xs.map(x=>Number(x.dataset.level)).sort((a,b)=>a-b)')
            assert levels==info['contentControls']['levels'];page.keyboard.press('Escape')
            result['liveChecks']['sharedControls']={'levelsMatchCachedSettings':True,'cachedExclusions':info['community']['cachedContentControls']}
            page.locator('#communityRecommend').click();assert page.locator('dialog[open]').count()==1
            assert page.locator('#communityForm input').count()==1
            page.screenshot(path=str(output/'recommend-modal.png'));page.keyboard.press('Escape')
            result['liveChecks']['singleModal']={'oneDialog':True,'oneInput':True,'noLiveSubmission':True}
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.screenshot(path=str(output/'community-mobile.png'))
            page.locator('#tabDiscovery').click();page.locator('#discoveryBody').wait_for(state='visible')
            result['liveChecks']['profile']={'cachedProfileVisible':True,'fullSynchronizationStarted':False}
            panel=page.locator('#developmentDiagnostics');panel.locator('summary').click()
            page.locator('#developmentDiagnosticsOutput').filter(has_text='gitCommit').wait_for()
            page.locator('#runDevelopmentDiagnostics').click()
            page.locator('#developmentDiagnosticsStatus').filter(has_text='Diagnostics complete').wait_for(timeout=15000)
            page.screenshot(path=str(output/'diagnostics-mobile.png'))
            result['liveChecks']['diagnostics']={'readOnly':True,'snapshot':json.loads(page.locator('#developmentDiagnosticsOutput').inner_text())}
            assert not errors,errors;browser.close()
        result['liveChecks']['browserErrors']=errors;result['liveChecks']['blockedIncidentalPosts']=sorted(set(blocked))
        after_data=critical_data(data);result['liveChecks']['criticalDataUnchanged']=before==after_data
        result['criticalDataBefore']=before;result['criticalDataAfter']=after_data
        assert before==after_data,'Critical records changed; inspect the snapshots before further testing.'
        save()
        # Every write is in fresh challenged sandboxes, never the interactive data root.
        suites=['community_backend','community_browser','community_ten_members','persistence_browser']
        command=[sys.executable,str(ROOT/'scripts/run_tests.py'),'--only',*suites,'--output',str(output/'isolated'),'--timeout','150']
        child=subprocess.run(command,cwd=ROOT,timeout=210)
        entries=json.loads((output/'isolated/results.json').read_text())
        result['isolatedTests']={'suites':entries,'syntheticIdentities':True,'passed':child.returncode==0}
        assert child.returncode==0,'An isolated smoke suite failed'
        result['passed']=True;save()
        print(json.dumps({'passed':True,'live':'real protected session and cached artwork',
            'isolated':'synthetic submissions/quotas/duplicates/offline persistence','screenshots':str(output.resolve())}),flush=True)
    except Exception as error:
        result['passed']=False;result['error']=str(error);save();raise


if __name__=='__main__':main()

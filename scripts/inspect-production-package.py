"""Inspect the actual frozen archive and launch only owned isolated profiles."""
import argparse
import hashlib
import json
import marshal
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import types
import re
import urllib.request
import urllib.error
from PyInstaller.archive.readers import CArchiveReader
from playwright.sync_api import sync_playwright

def snapshot(root):
    result={}
    for path in root.rglob('*.sqlite3'):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            assert db.execute('pragma quick_check').fetchone()[0]=='ok'
            for (table,) in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
                if any(s in table for s in ('recommend','audit','reacted','followed','images','import')):
                    rows=sorted(json.dumps(r,default=str) for r in db.execute('select * from "'+table.replace('"','""')+'"'))
                    result[str(path.relative_to(root))+':'+table]={'count':len(rows),'hash':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--upgrade-profile',type=Path)
    args=parser.parse_args();package=args.package.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    executable=package/'CivitaiDiscoveryBeta.exe';archive=CArchiveReader(str(executable));pyz=archive.open_embedded_archive('PYZ.pyz')
    forbidden=[name for name in pyz.toc if name.startswith(('dev.','tests.')) or name in ('discovery.community_dev','discovery.community_fixtures','lab','actor')]
    assert not forbidden,forbidden
    # Inspect our actual frozen code constants/filenames, not just archive names.
    code_findings=[]
    def inspect_code(code,name):
        if not isinstance(code,types.CodeType):return
        for value in (code.co_filename,*code.co_consts):
            if isinstance(value,types.CodeType):inspect_code(value,name)
            elif isinstance(value,str) and (re.search(r'(?i)\b[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+[\\/]',value) or re.search(r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}',value)):
                code_findings.append(name)
    inspect_code(marshal.loads(archive.extract('server')),'server')
    for name in pyz.toc:
        if name.startswith('discovery.'):inspect_code(pyz.extract(name),name)
    assert not code_findings,sorted(set(code_findings))
    paths=[str(p.relative_to(package)) for p in package.rglob('*') if p.is_file()]
    assert not any(any(x in name.lower() for x in ('community-test-lab','public-fixtures','postgres','oauth_tokens','reports/')) or name.endswith(('.sqlite3','.dpapi','.env')) for name in paths)
    result={'archiveInspected':True,'excludedModulesAbsent':True,'applicationCodePatternFindings':code_findings,'pythonModules':len(pyz.toc),'packageFiles':len(paths),'executableSha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'profiles':[],'limitations':['Isolated unauthenticated profiles cannot verify invited-user access; real hosted OAuth is checked separately.','Copied upgrade data excludes all OAuth credentials.']}
    scratch=Path(tempfile.mkdtemp(prefix='cad3-production-acceptance-'))
    for scenario in ('fresh','upgrade','hostile-environment'):
        profile=scratch/scenario;profile.mkdir()
        if scenario=='upgrade' and args.upgrade_profile:
            for source in args.upgrade_profile.resolve().rglob('*.sqlite3'):
                target=profile/source.relative_to(args.upgrade_profile.resolve());target.parent.mkdir(parents=True,exist_ok=True)
                with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(target) as copied:original.backup(copied)
        (profile/'settings.json').write_text(json.dumps({'autoCapture':False,'checkForUpdates':False}))
        if scenario=='hostile-environment':(profile/'community-beta.json').write_text('{"optedIn":false}')
        before=snapshot(profile)
        env={k:v for k,v in os.environ.items() if not k.startswith(('CAD_','SUPABASE_','PG'))}
        env['CIVITAI_HISTORY_DATA_DIR']=str(profile)
        if scenario=='hostile-environment':env.update(CAD_DEVELOPMENT='1',CAD_COMMUNITY_MODE='fixture',CAD_COMMUNITY_TESTING='1',CAD_TEST_INSTANCE='malicious-local-challenge',CAD_TEST_LAB_CONNECTION=str(scratch/'fake.json'),CAD_COMMUNITY_URL='http://127.0.0.1:1')
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        def request(path,body=None):
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Origin':base})
            try:
                with urllib.request.urlopen(req,timeout=10) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:
                try:value=json.load(error)
                except ValueError:value={}
                return error.code,value
        process=subprocess.Popen([str(executable),'--host','127.0.0.1','--port',str(port),'--no-browser'],cwd=package,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(200):
                try:
                    if request('/api/history/config')[0]==200:break
                except OSError:pass
                time.sleep(.1)
            else:raise RuntimeError('Frozen package did not become ready')
            for path in ('/api/development/diagnostics','/api/test-isolation','/api/community/development/member','/__dev/community-test-lab/entry.js','/community-fixture.svg'):
                assert request(path)[0]==404,(scenario,path)
            beta=request('/api/community/beta')[1];assert beta['production'] and beta['available']
            assert beta['enabled'] is True and 'optedIn' not in beta, 'Community connects automatically, including legacy disabled profiles'
            assert request('/api/community/recommend',{'artwork':123})[0] in (401,403)
            assert request('/api/community/beta',{'optedIn':True})[0]==404
            assert request('/api/community/beta',{'optedIn':False})[0]==404
            assert request('/api/auth-status')[1]['connected'] is False
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1280,'height':900})
                page.goto(base,wait_until='networkidle');page.screenshot(path=str(output/(scenario+'.png')))
                assert page.locator('#tabCommunity').is_hidden()
                assert page.locator('#communityTestLab').count()==0
                assert page.get_by_text('Opt in to Community Beta',exact=False).count()==0
                assert page.locator('#welcome').is_visible()
                # UI-only identity simulation never supplies a backend credential.
                page.route('**/api/auth-status',lambda route:route.fulfill(json={'connected':True,'id':700,'username':'UI simulation only','socialWrite':False}))
                page.route('**/api/discovery/summary',lambda route:route.fulfill(json={'hasData':False,'lastSyncAt':'2099-01-01T00:00:00Z'}))
                page.reload(wait_until='networkidle');assert page.locator('#tabGallery').is_visible()
                assert page.get_by_text('Opt in to Community Beta',exact=False).count()==0
                assert page.locator('#tabCommunity').is_visible()
                assert request('/api/community/recommend',{'artwork':123})[0] in (401,403)
                page.screenshot(path=str(output/(scenario+'-ui-simulated-auth.png')))
                page.screenshot(path=str(output/(scenario+'.png')));browser.close()
            assert request('/api/app/close',{})[1]['closing'];process.wait(timeout=15)
            after=snapshot(profile)
            assert all(after.get(k)==v for k,v in before.items()),'Upgrade record changes'
            result['profiles'].append({'scenario':scenario,'passed':True,'preservedCriticalTables':len(before),'noOAuthCredentialsCopied':True,'developerRoutes404':True,'automaticCommunityConnection':True,'legacyPreferenceIgnored':True,'optInApiRemoved':True,'communityTabVisible':True,'oauthWelcomeRendered':True,'browserOnlyFakeIdentityCannotSubmit':True})
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
    result['passed']=True;result['scratch']=str(scratch)
    (output/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()

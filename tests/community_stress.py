"""Ten-member baseline, persisted-state adversarial tests and isolated single-modal browser suites."""
from __future__ import annotations
import test_safety
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from community_harness import Harness
from test_safety import ROOT, IsolatedEndpoint


class Stress:
    def __init__(self,harness):
        self.h=harness
        self.manifest=json.loads((harness.root/'sample.json').read_text(encoding='utf-8'))
        self.report={'source':self.manifest['sourceStats'],'members':[],'attempts':[], 'scenarios':[],
                     'browser':{},'limitations':['No live Civitai ownership or deletion verification',
                        'No PostgreSQL/RLS/Edge runtime execution; SQLite concurrency is separate']}
        self.plan=[];artists=Counter();used=set()
        for n in range(30):
            rating=(1,2,4,8,16)[n%5]
            record=next(r for r in self.manifest['artworks'] if r['id'] not in used
                and r['browsingLevel']==rating and artists[r['creatorId']]<3)
            self.plan.append(record);used.add(record['id']);artists[record['creatorId']]+=1
        self.extra=next(r for r in self.manifest['artworks'] if r['id'] not in used and artists[r['creatorId']]<3)

    def save(self):
        target=test_safety.REPORTS/'community-stress.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(self.report,indent=2),encoding='utf-8')

    def recommend(self,member,image,expected='OK',*,accepted=1,scenario='baseline'):
        before=self.h.snapshot()
        try:
            result=self.h.endpoint.request('/api/community/recommend',{'artwork':image},member=member)
            code='OK'
        except urllib.error.HTTPError as error:
            result=json.load(error);code=result['code']
        after=self.h.snapshot()
        actual=len(after['recommendations'])-len(before['recommendations'])
        entry={'scenario':scenario,'member':member,'artwork':image,'code':code,
            'acceptedDelta':actual,'rowsAfter':len(after['recommendations']),
            'auditAfter':len(after['audit'])}
        self.report['attempts'].append(entry);self.save()
        assert code==expected,(entry,expected)
        assert actual==accepted,entry
        if not accepted:assert before==after,entry
        return result

    def baseline(self):
        self.h.reset()
        for member in range(1,11):
            for row in self.plan[(member-1)*3:member*3]:self.recommend(member,row['id'])
            self.recommend(member,self.extra['id'],'MEMBER_QUOTA',accepted=0)
            status=self.h.endpoint.request('/api/community/status',member=member)
            assert status['quota']['used']==3 and status['quota']['remaining']==0
            self.report['members'].append({'id':member,'name':status['member']['username'],
                'accepted':3,'fourth':'MEMBER_QUOTA','remaining':0})
        state=self.h.snapshot();counts=Counter(r['creator_id'] for r in state['recommendations'])
        assert len(state['recommendations'])==30 and len(state['audit'])==30 and max(counts.values())<=3
        self.report['artistCounts']={str(k):v for k,v in counts.items()}
        self.report['baselineRecords']=state['recommendations']
        self.report['baselineAudit']=state['audit']
        rows=[];cursor=None;pages=0
        while True:
            page=self.h.endpoint.request('/api/community/gallery'+('' if cursor is None else '?cursor='+cursor))
            rows.extend(page['items']);pages+=1;cursor=page['nextCursor']
            if cursor is None:break
        assert len(rows)==len({r['imageId'] for r in rows})==30
        assert {r['artwork']['browsingLevel'] for r in rows}=={1,2,4,8,16}
        assert all(r['recommender']==f"DemoMember{1+self.plan.index(next(x for x in self.plan if x['id']==r['imageId']))//3:02d}" for r in rows)
        self.report['pagination']={'pages':pages,'visible':len(rows),'unique':len({r['imageId'] for r in rows})}
        self.save();print('PASS: 10 members, 30 accepted, 10 fourth rejections, 5 ratings, pagination',flush=True)

    def adversarial(self):
        def case(name,fn):
            self.h.reset();fn();state=self.h.snapshot()
            self.report['scenarios'].append({'name':name,'passed':True,'database':state})
            self.save();print('PASS:',name,flush=True)
        case('self recommendation',lambda:self.recommend(1,1099,'SELF_RECOMMENDATION',accepted=0,scenario='self'))
        def duplicate():
            self.recommend(1,1001);self.recommend(2,1001,'DUPLICATE',accepted=0,scenario='duplicate')
        case('two members same artwork',duplicate)
        def artist():
            for member,image in ((1,1001),(2,1002),(3,1003)):self.recommend(member,image)
            self.recommend(4,1004,'ARTIST_QUOTA',accepted=0,scenario='artist')
        case('four members one artist',artist)
        def fourth():
            for image in (1001,1006,1011):self.recommend(1,image)
            self.recommend(1,1016,'MEMBER_QUOTA',accepted=0,scenario='member')
        case('member fourth recommendation',fourth)
        case('nonexistent image',lambda:self.recommend(1,999999999999,'INVALID_ARTWORK',accepted=0,scenario='missing'))
        def urls():
            for value in ('https://evil.test/images/1001','https://civitai.red@127.0.0.1/images/1001',
                          'http://127.0.0.1:8906/','https://civitai.red/images/%31','https://civitai.red/images/1001?x=1','01'):
                self.recommend(1,value,'INVALID_ARTWORK',accepted=0,scenario='malicious')
        case('malformed and malicious URLs',urls)
        for row in self.manifest['negativeCases']:
            case(row['state'],lambda row=row:self.recommend(1,row['id'],
                'UNVERIFIED_OWNER' if row['state'] in ('missing_creator','unverified_owner') else 'INVALID_ARTWORK',
                accepted=0,scenario=row['state']))
        def unavailable():
            self.h.control('failure',code='UNAVAILABLE');self.recommend(1,1001,'UNAVAILABLE',accepted=0,scenario='unavailable')
        case('upstream unavailable',unavailable)
        def expires():
            self.h.control('failure',code=None,duringArtwork=True)
            self.recommend(1,1001,'AUTH_EXPIRED',accepted=0,scenario='expired_during_submission')
        case('authentication expires during submission',expires)
        def race(artist):
            if artist:
                self.recommend(1,1001);self.recommend(2,1002)
                inputs=[(3,1003),(4,1004)]
            else:inputs=[(1,1001),(1,1006),(1,1011),(1,1016),(1,1021),(1,1026)]
            def submit(pair):
                try:self.h.endpoint.request('/api/community/recommend',{'artwork':pair[1]},member=pair[0]);return 'OK'
                except urllib.error.HTTPError as error:return json.load(error)['code']
            with ThreadPoolExecutor(max_workers=len(inputs)) as pool:codes=list(pool.map(submit,inputs))
            assert codes.count('OK')==(1 if artist else 3),codes
            state=self.h.snapshot();assert len(state['recommendations'])==len(state['audit'])==3
            assert set(codes)<=({'OK','ARTIST_QUOTA'} if artist else {'OK','MEMBER_QUOTA'})
            self.report.setdefault('concurrency',[]).append({'race':'last artist slot' if artist else 'same member',
                'attempts':[{'member':m,'image':i,'code':c} for (m,i),c in zip(inputs,codes)],'database':state})
        case('two members race for last artist slot',lambda:race(True))
        case('one member simultaneous submissions',lambda:race(False))
        def lost():
            self.h.control('loseConfirmationResponse')
            self.recommend(1,1001,'UNAVAILABLE',accepted=1,scenario='lost_response_after_commit')
            self.recommend(1,1001,'DUPLICATE',accepted=0,scenario='retry_after_timeout')
            assert self.h.endpoint.request('/api/community/status')['quota']['used']==1
        case('confirmation response lost after commit',lost)
        case('duplicate confirmation',duplicate)
        def monday():
            self.h.control('clock',now='2026-10-18T23:59:59+00:00');fourth()
            self.h.control('clock',now='2026-10-19T00:00:00+00:00')
            assert self.h.endpoint.request('/api/community/status')['quota']['used']==0
            self.recommend(1,1016)
            assert self.h.endpoint.request('/api/community/status')['quota']['used']==1
        case('Monday UTC reset',monday)
        def hidden():
            for image in (1001,1002,1006):self.recommend(1,image)
            controls={'hiddenUsers':[{'id':101,'hidden':True}], 'hiddenImages':[1006],
                'blockedUsers':[],'blockedByUsers':[],'hiddenTags':[]}
            self.h.control('hidden',member=2,controls=controls)
            gallery=self.h.endpoint.request('/api/community/gallery',member=2)
            assert gallery['items']==[] and 'thumbnailUrl' not in json.dumps(gallery)
            assert len(self.h.snapshot()['recommendations'])==3
        case('hidden creators and images never serialized',hidden)
        def tags():
            self.recommend(1,1001)
            controls={'hiddenUsers':[], 'hiddenImages':[], 'blockedUsers':[{'id':101,'hidden':True}],
                'blockedByUsers':[],'hiddenTags':[{'id':11,'name':'landscape','hidden':True}]}
            self.h.control('hidden',member=2,controls=controls)
            assert self.h.endpoint.request('/api/community/gallery',member=2)['items']==[]
        case('blocked users and restricted tags',tags)
        def removed():
            self.recommend(1,1001);self.h.control('removed',image=1001)
            assert self.h.endpoint.request('/api/community/gallery')['items']==[]
            assert len(self.h.snapshot()['recommendations'])==1
        case('removed artwork disappears without erasing audit',removed)
        def withdraw():
            self.recommend(1,1001)
            self.h.endpoint.request('/api/community/withdraw',{'artwork':1001},member=1)
            state=self.h.snapshot();assert state['recommendations'][0]['status']=='withdrawn'
            assert len(state['audit'])==2 and self.h.endpoint.request('/api/community/status')['quota']['used']==1
        case('withdrawal retains accepted quota',withdraw)
        def offline():
            self.recommend(1,1001);self.h.control('offline',enabled=True)
            self.recommend(1,1006,'UNAVAILABLE',accepted=0,scenario='offline')
            personal=self.h.endpoint.request(f'/api/history/artists?date={self.h.day}&segment=all&view=discovery')
            assert len(personal['artists'])==12
            assert 'hasData' in self.h.endpoint.request('/api/discovery/summary')
        case('community outage preserves personal archive and profile',offline)

    def safety(self):
        for target in ('http://127.0.0.1:8906','https://example.com','https://test.supabase.co','http://127.0.0.1:33201'):
            with patch('socket.create_connection',side_effect=AssertionError('Network attempted')):
                try:IsolatedEndpoint(target,self.h.instance,self.h.root);raise AssertionError('Unsafe target accepted')
                except PermissionError:pass
        before=self.h.snapshot()
        endpoint=IsolatedEndpoint(self.h.base,'x'*48,self.h.root)
        try:endpoint.verify();raise AssertionError('Wrong instance accepted')
        except PermissionError:pass
        try:endpoint.request('/api/settings',{'browsingLevels':[16]});raise AssertionError('Unverified mutation accepted')
        except PermissionError:pass
        try:self.h.endpoint.request('/api/community/recommend',{'artwork':1001},member=11);raise AssertionError('Unknown member accepted')
        except urllib.error.HTTPError as error:assert error.code==401
        assert self.h.snapshot()==before
        self.report['safety']={'protectedPortsRejectedBeforeNetwork':True,'productionRejected':True,
            'nonceMismatchRejected':True,'unknownMemberRejected':True,'serverOutboundDisabled':True}
        self.save()

    def browser(self):
        # Supersedes the removed welcome bypass and two-stage preview/confirm UI.
        # Each entry point owns a fresh challenged root and ephemeral port.
        import subprocess
        for script in ('community_browser.py','community_ten_members.py'):
            subprocess.run([sys.executable,str(ROOT/'tests'/script)],check=True,timeout=120)
        self.report['browser']={'passed':True,'suites':['community_browser.py','community_ten_members.py'],
            'authentication':'isolated challenge; mocked frontend identity','realCivitaiWrites':False}
        self.save()


def run():
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    h=Harness(port=port);stress=None
    try:
        h.seed_personal();h.start();stress=Stress(h)
        stress.baseline();stress.adversarial();h.reset();stress.safety();stress.browser()
        stress.report['passed']=True;stress.save()
    finally:
        if stress:stress.save()
        h.stop()

if __name__=='__main__':run()

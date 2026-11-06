"""Isolated submission transaction, filter reconciliation and identity contracts."""
import test_safety
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(test_safety.ROOT))
from discovery.community import (Artwork, CommunityClient, CommunityError, ContentProfile,
    CivitaiVerifier, FixtureVerifier, LocalCommunityBackend, RateLimiter, content_profile)

class SubmissionContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.verifier=FixtureVerifier(development=True)
        self.path=Path(self.temp.name)/'ledger.sqlite3'
        self.backend=LocalCommunityBackend(self.path,self.verifier)
    def submit(self,image=1001,key='same-request-key-0001',member=1):
        return self.backend.request('recommend',f'fixture:{member}',{'artwork':image,'idempotencyKey':key})
    def counts(self):
        with self.backend.connect() as db:return tuple(db.execute('select (select count(*) from recommendations),(select count(*) from audit),(select count(*) from submission_requests)').fetchone())
    def test_concurrent_retries_restart_and_key_collision(self):
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:self.submit(),range(8)))
        self.assertEqual(sum(bool(r.get('replayed')) for r in results),7)
        self.assertEqual(self.counts(),(1,1,1))
        self.backend=LocalCommunityBackend(self.path,self.verifier)
        self.assertTrue(self.submit()['replayed'])
        for image,key,member,code in [(1006,'same-request-key-0001',1,'INVALID_REQUEST'),(1001,'same-request-key-0001',2,'DUPLICATE')]:
            with self.assertRaises(CommunityError) as error:self.submit(image,key,member)
            self.assertEqual(error.exception.code,code)
        self.assertEqual(self.counts(),(1,1,1))
    def test_idempotency_failure_rolls_back_ledger_audit_quota(self):
        with self.backend.connect() as db:db.execute("create trigger reject_key before insert on submission_requests begin select raise(abort,'injected storage failure'); end")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.submit()
        self.assertEqual(self.counts(),(0,0,0))
        self.assertEqual(self.backend.request('status','fixture:1',{})['quota']['used'],0)
    def test_30_records_23_tag_exclusions_7_visible_across_pages(self):
        arts=list(self.verifier.artworks.values())[:30]
        for i,art in enumerate(arts):self.verifier.artworks[art.id]=replace(art,tags=('restricted',) if i<23 else (),tag_ids=(99,) if i<23 else ())
        with self.backend.connect() as db:
            db.executemany("insert into recommendations(image_id,creator_id,member_id,member_name,submitted_at,week) values(?,?,?,?,?,?)",[(a.id,a.creator_id,1+i//3,'Simulated',datetime.now(timezone.utc).isoformat(),'2026-10-12') for i,a in enumerate(arts)])
        profile=ContentProfile(levels=frozenset((1,2,4,8,16)),tags=frozenset(('restricted',)),tag_ids=frozenset((99,)))
        client=CommunityClient(self.backend,self.verifier,lambda:'fixture:1',lambda _:profile)
        pages=[];cursor=None
        while True:
            result=client.request('gallery',{'cursor':cursor});pages.append(result);cursor=result['nextCursor']
            if not cursor:break
        self.assertEqual(sum(p['counts']['acceptedPage'] for p in pages),30)
        self.assertEqual(sum(p['counts']['contentControls'] for p in pages),23)
        self.assertEqual(sum(p['counts']['exclusions']['hiddenTag'] for p in pages),23)
        self.assertEqual(sum(len(p['items']) for p in pages),7)
    def test_all_filter_reasons_and_incomplete_metadata_fail_closed(self):
        hidden={'hiddenUsers':[{'id':42,'username':'Artist'}], 'hiddenTags':[{'id':99,'name':'restricted'}],
            'hiddenImages':[1], 'hiddenImagesImplicit':[2], 'blockedUsers':[{'id':43,'username':'Blocked'}], 'blockedByUsers':[]}
        p=content_profile(hidden,(1,2))
        art=Artwork(1,42,'Artist',8,'',('restricted',),(99,))
        self.assertEqual(set(p.exclusions(art)),{'ratingRestriction','hiddenCreator','hiddenArtwork','hiddenTag'})
        self.assertIn('blockedUser',p.exclusions(replace(art,id=3,creator_id=43,username='Blocked')))
        self.assertIn('incompleteMetadata',p.exclusions(replace(art,id=3,tags_known=False,tags=(),tag_ids=())))
        self.assertFalse(p.allows(replace(art,id=2,creator_id=44,username='Other',rating=1,tags=(),tag_ids=())))
    def test_oauth_is_only_sent_to_fixed_api_endpoints(self):
        calls=[]
        def fetch(url,token=None):
            calls.append((url,token))
            if '/api/v1/' in url:return {'items':[{'id':123,'type':'image','username':'Artist','browsingLevel':1,'url':'https://image.civitai.com/x/y/width=512/a.jpg'}]}
            detail={'id':123,'user':{'id':42,'username':'Artist'},'availability':'Public','ingestion':'Scanned','needsReview':None,'publishedAt':'2026-01-01T00:00:00Z','nsfwLevel':1}
            return {'result':{'data':{'json':[] if 'tag.get' in url else detail}}}
        art=CivitaiVerifier(fetch,token_provider=lambda:'synthetic-only').artwork(123)
        self.assertEqual(art.creator_id,42)
        self.assertEqual(len(calls),3)
        self.assertTrue(all(url.startswith('https://civitai.red/api/') and token=='synthetic-only' for url,token in calls))
    def test_production_rejects_fixture_override_before_backend(self):
        client=CommunityClient(self.backend,CivitaiVerifier(),lambda:'not-used',lambda _:ContentProfile())
        client.mode='supabase'
        with self.assertRaises(CommunityError) as error:client.request('recommend',{'artwork':1001},fixture_token='fixture:1')
        self.assertEqual(error.exception.code,'FORBIDDEN');self.assertEqual(self.counts(),(0,0,0))

if __name__=='__main__':unittest.main()

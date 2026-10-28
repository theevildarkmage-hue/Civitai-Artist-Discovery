"""Deterministic community rules, network shapes, privacy and concurrency tests."""

import test_safety  # Fail closed before any test network request.
import concurrent.futures
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.community import (Artwork, CommunityClient, CommunityError, ContentProfile,
    CivitaiVerifier, FixtureVerifier, LocalCommunityBackend, RateLimiter, SupabaseCommunityBackend,
    bounded_json, build_community, parse_artwork, content_profile)


class CommunityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.verifier = FixtureVerifier(development=True)
        self.now = datetime(2026, 10, 18, 23, 59, 59, tzinfo=timezone.utc)
        self.backend = LocalCommunityBackend(Path(self.temp.name)/'community.sqlite3', self.verifier, clock=lambda:self.now)
        self.backend.rate = RateLimiter(1000)

    def request(self, image, member=1, operation='recommend'):
        return self.backend.request(operation, f'fixture:{member}', {'artwork':image})

    def assertCode(self, code, function, *args):
        with self.assertRaises(CommunityError) as error:
            function(*args)
        self.assertEqual(code, error.exception.code)

    def used(self, member=1):
        return self.backend.request('status', f'fixture:{member}', {})['quota']['used']

    def test_model_projection_and_old_cache_compatibility(self):
        legacy={'id':1,'creator_id':2,'username':'Synthetic','rating':1,'preview':'https://image.civitai.com/example'}
        self.assertEqual(Artwork(**legacy).browser()['baseModel'],'Unknown')
        projected=Artwork(**legacy,base_model='Flux.1 D | YuE2-3B').browser()
        self.assertEqual(projected['modelLabels'],('Flux.1 D','YuE2-3B'))
        self.assertEqual(projected['type'],'image')

    def test_valid_and_preview_does_not_consume(self):
        self.request(1001, operation='verify'); self.assertEqual(0,self.used())
        self.assertEqual(1,self.request(1001)['quota']['used'])
        self.assertCode('DUPLICATE',self.request,1001,2); self.assertEqual(0,self.used(2))

    def test_self_rejected(self):
        self.assertCode('SELF_RECOMMENDATION',self.request,1099); self.assertEqual(0,self.used())

    def test_member_quota(self):
        for image in (1001,1006,1011): self.request(image)
        self.assertCode('MEMBER_QUOTA', self.request,1016); self.assertEqual(3,self.used())

    def test_artist_three_not_one(self):
        for image, member in ((1001,1),(1002,2),(1003,3)): self.request(image,member)
        self.assertCode('ARTIST_QUOTA',self.request,1004,4); self.assertEqual(0,self.used(4))

    def test_week_reset(self):
        for image in (1001,1006,1011): self.request(image)
        self.now += timedelta(seconds=1)
        self.assertEqual(0,self.used()); self.request(1016)
        self.assertEqual(1,self.used())
        self.assertCode('DUPLICATE',self.request,1001,2)

    def test_artist_reset_and_utc_offset(self):
        for image,member in ((1001,1),(1002,2),(1003,3)): self.request(image,member)
        self.now = datetime(2026,10,18,19,0,tzinfo=timezone(timedelta(hours=-5)))
        self.request(1004,4)

    def test_withdraw_and_moderation_keep_slots_and_audit(self):
        for image in (1001,1006,1011): self.request(image)
        self.request(1001, operation='withdraw')
        self.backend.moderate(1006,'hidden'); self.backend.moderate(1011,'removed')
        self.assertCode('MEMBER_QUOTA', self.request,1016)
        self.assertEqual([],self.backend.request('gallery','fixture:1',{})['items'])
        with self.backend.connect() as db:
            self.assertEqual(6,db.execute('SELECT count(*) FROM audit').fetchone()[0])

    def test_withdraw_requires_author(self):
        self.request(1001); self.assertCode('FORBIDDEN', self.request,1001,2,'withdraw')

    def test_invalid_inputs(self):
        for value in (0,-1,True,1.2,'01','1e2','https://evil.test/images/1001',
            'https://civitai.red@evil.test/images/1001','https://civitai.red:443/images/1001',
            'https://civitai.red/images/1001?url=http://127.0.0.1',
            'https://civitai.red/images/%31','http://civitai.red/images/1001',
            'https://civitai.red.evil.test/images/1001','https://civitai.red/images/1001#x',
            'file:///etc/passwd',{},'9'*1000):
            self.assertCode('INVALID_ARTWORK',parse_artwork,value)
        self.assertEqual(1001,parse_artwork('https://www.civitai.com/images/1001/'))
        self.assertCode('INVALID_ARTWORK',self.request,999999)
        self.assertEqual(0,self.used())

    def test_simultaneous_member_limit_separate_connections(self):
        images=(1001,1006,1011,1016,1021,1026)
        def submit(image):
            backend=LocalCommunityBackend(self.backend.path,self.verifier,clock=lambda:self.now)
            try: backend.request('recommend','fixture:1',{'artwork':image}); return 'OK'
            except CommunityError as error: return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(submit,images))
        self.assertEqual(3,results.count('OK')); self.assertEqual(3,results.count('MEMBER_QUOTA'))

    def test_simultaneous_artist_and_duplicate(self):
        def submit(pair):
            try: self.request(*pair); return 'OK'
            except CommunityError as error: return error.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            result=list(pool.map(submit,[(1001+i,i+1) for i in range(5)]))
        self.assertEqual(3,result.count('OK'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            result=list(pool.map(submit,[(1011,i+1) for i in range(5)]))
        self.assertEqual(1,result.count('OK')); self.assertEqual(4,result.count('DUPLICATE'))

    def test_privacy_and_removed_art(self):
        for i in (1001,1002,1003): self.request(i)
        profile=ContentProfile(levels=frozenset((1,2,4)))
        client=CommunityClient(self.backend,self.verifier,lambda:'fixture:2',lambda _:profile)
        self.assertEqual(3,len(client.request('gallery',{})['items']))
        profile=ContentProfile(levels=frozenset((1,)),images=frozenset((1001,)))
        result=client.request('gallery',{})
        self.assertEqual([],result['items']); self.assertNotIn('thumbnail',json.dumps(result))
        self.assertCode('CONTENT_FILTERED',client.request,'verify',{'artwork':1001})
        for profile in (ContentProfile(creators=frozenset((101,))),
                        ContentProfile(usernames=frozenset(('demoartist1',))),
                        ContentProfile(tags=frozenset(('landscape','portrait'))),
                        ContentProfile(tag_ids=frozenset((11,12)))):
            self.assertEqual([],client.request('gallery',{})['items'])
        profile=ContentProfile(levels=frozenset((1,2,4)))
        del self.verifier.artworks[1001]
        self.assertEqual(2,len(client.request('gallery',{})['items']))

    def test_ratings_all_supported(self):
        for level in (1,2,4,8,16):
            art=self.verifier.artworks[1001+([1,2,4,8,16].index(level))]
            self.assertTrue(ContentProfile(levels=frozenset((level,))).allows(art))

    def test_large_implicit_exclusions_reuse_index(self):
        class CountedPairs(frozenset):
            visits = 0

            def __iter__(self):
                for pair in super().__iter__():
                    self.visits += 1
                    yield pair

        pairs = CountedPairs((image, 11) for image in range(1, 8001))
        profile = ContentProfile(tag_ids=frozenset((11,)), implicit_images=pairs)
        expected = set(range(1, 8001))
        self.assertEqual(expected, profile.excluded_image_ids())
        for image in expected:
            self.assertEqual(['hiddenTag'], profile.image_exclusions(image))
        # Bound work, not wall-clock time: bulk and individual reads share one index.
        self.assertEqual(8000, pairs.visits)
        self.assertEqual(set(), replace(profile, apply_tag_filters=False).excluded_image_ids())

    def test_index_preserves_overlapping_sources_and_rating_ceiling(self):
        profile = ContentProfile(images=frozenset((1001,)), tag_ids=frozenset((11,)),
            implicit_images=frozenset(((1001, 11), (1001, 99), (1002, 99), (1003, 12))),
            rating_tags=frozenset(((99, 4),)))
        self.assertEqual(['hiddenArtwork', 'hiddenTag', 'ratingRestriction'],
                         profile.image_exclusions(1001))
        self.assertEqual({1001, 1002, 1003}, profile.excluded_image_ids())
        paused = replace(profile, apply_tag_filters=False, apply_image_filters=False)
        self.assertEqual({1001, 1002, 1003}, paused.excluded_image_ids())
        self.assertEqual({1003}, replace(paused, levels=frozenset((1, 4))).excluded_image_ids())

    def test_unclassified_implicit_hides_cannot_be_relaxed_by_switches(self):
        hidden = {'hiddenUsers': [], 'blockedUsers': [], 'blockedByUsers': [],
            'hiddenTags': [{'id': 11, 'name': 'topic'}, {'id': 99, 'name': 'rating', 'nsfwLevel': 4},
                           {'id': 12, 'name': 'unhidden-topic', 'hidden': False}],
            'hiddenImages': [1000, {'id': 1009, 'tagId': 999}, {'id': 1012, 'tagId': 0}],
            'hiddenImagesImplicit': [1001, {'id': 1002}, {'id': 1003, 'tagId': None},
                {'id': 1004, 'tagId': 999}, {'id': 1005, 'tagId': 11}, {'id': 1006, 'tagId': 99},
                {'id': 1007, 'tagId': 11, 'hidden': False}, {'id': 1008, 'tagId': 12},
                {'id': 1010, 'hidden': False}, {'id': 1011, 'tagId': False}]}
        profile = content_profile(hidden, (1, 2))
        unknown = {1001, 1002, 1003, 1004, 1009, 1011, 1012}
        self.assertEqual(unknown | {1000, 1005, 1006}, profile.excluded_image_ids())
        paused = replace(profile, apply_tag_filters=False, apply_image_filters=False,
                         levels=frozenset((1, 2, 4, 8, 16)))
        self.assertEqual(unknown, paused.excluded_image_ids())
        for image in unknown:
            self.assertEqual(['incompleteMetadata'], paused.image_exclusions(image))

    def test_upstream_compact_implicit_and_bidirectional_blocks(self):
        hidden={'__v':2,'hiddenUsers':[], 'hiddenTags':[{'id':11,'name':'landscape','hidden':True}], 'hiddenImages':[],
                'hiddenImagesImplicit':[{'id':1001,'tagId':11,'hidden':True}],
                'blockedUsers':[{'id':102,'username':'DemoArtist2','hidden':True}],
                'blockedByUsers':[{'id':103,'username':'DemoArtist3','hidden':True}]}
        profile=content_profile(hidden,(1,2,4,8,16))
        for image in (1001,1006,1011): self.assertFalse(profile.allows(self.verifier.artworks[image]))
        self.assertTrue(profile.allows(self.verifier.artworks[1016]))
        hidden['hiddenImages']=[1002]
        self.assertFalse(content_profile(hidden,(1,2)).allows(self.verifier.artworks[1002]))
        hidden['blockedUsers']=None
        self.assertCode('UNAVAILABLE',content_profile,hidden,(1,2))

    def test_network_expiration_and_rate_limit(self):
        for failure in ('UNAVAILABLE','AUTH_EXPIRED','RATE_LIMITED'):
            self.verifier.failure=failure
            self.assertCode(failure,self.request,1001)
        self.verifier.failure=None; self.assertEqual(0,self.used())
        limiter=RateLimiter(1); limiter.check('one'); self.assertCode('RATE_LIMITED',limiter.check,'one')
        with patch('urllib.request.OpenerDirector.open',side_effect=TimeoutError()):
            self.assertCode('UNAVAILABLE',bounded_json,'https://civitai.red/api/v1/images')

    def test_fixture_guard_and_production_url(self):
        with self.assertRaises(RuntimeError): FixtureVerifier()
        with patch.dict(os.environ,{'CAD_COMMUNITY_MODE':'fixture','CAD_DEVELOPMENT':'0'}):
            with self.assertRaises(RuntimeError): build_community(Path(self.temp.name),lambda _:None,lambda:None)
        for url in ('http://localhost','https://evil.test/functions/v1/community-v1'):
            with self.assertRaises(ValueError): SupabaseCommunityBackend(url)

    def test_pagination_no_duplicate_rows(self):
        for i,member in ((1001,1),(1002,2),(1003,3),(1006,4),(1007,5)):
            self.request(i,member)
        cursor=None; ids=[]
        for _ in range(3):
            page=self.backend.request('gallery','fixture:1',{'limit':2,'cursor':cursor})
            ids += [r['imageId'] for r in page['items']]; cursor=page['nextCursor']
        self.assertEqual(5,len(set(ids))); self.assertIsNone(cursor)


class RealVerificationTests(unittest.TestCase):
    def setUp(self):
        self.item={'id':123,'type':'image','username':'Artist','browsingLevel':8,
                   'url':'https://image.civitai.com/test/width=640/art.jpeg'}
        self.detail={'id':123,'user':{'id':42,'username':'Artist','deletedAt':None},
                     'availability':'Public','ingestion':'Scanned','needsReview':None,
                     'publishedAt':'2026-01-01T00:00:00Z','nsfwLevel':8}
        def fetch(url,token=None):
            if '/userinfo' in url: return {'sub':'7','id':7,'username':'Member'}
            if '/api/v1/' in url: return {'items':[self.item]}
            return {'result':{'data':{'json':[] if 'tag.get' in url else self.detail}}}
        self.verifier=CivitaiVerifier(fetch)

    def test_authoritative_owner_and_member(self):
        self.assertEqual(42,self.verifier.artwork(123).creator_id)
        self.assertEqual(7,self.verifier.member('test-only').id)

    def test_video_uses_same_authoritative_eligibility(self):
        self.item.update(type='video',url='https://image.civitai.com/test/original=true/clip.mp4')
        self.detail['type']='video'
        art=self.verifier.artwork(123)
        self.assertEqual('video',art.media_type)
        value=art.browser()
        self.assertIn('/anim=false,transcode=true,width=768/',value['thumbnailUrl'])
        self.assertIn('/transcode=true,width=450,optimized=true/',value['videoUrl'])
        self.assertIn('/transcode=true,width=768,optimized=true/',value['detailVideoUrl'])
        for field,value in (('availability','Unsearchable'),('ingestion','Pending'),('nsfwLevel',1),('type','image')):
            old=self.detail[field];self.detail[field]=value
            with self.assertRaises(CommunityError):self.verifier.artwork(123)
            self.detail[field]=old
        self.item['type']='audio'
        with self.assertRaises(CommunityError):self.verifier.artwork(123)

    def test_mismatch_missing_owner_and_unavailable(self):
        for mutate,code in ((lambda:self.item.update(id=124),'INVALID_ARTWORK'),
            (lambda:self.detail.update(user={}), 'UNVERIFIED_OWNER'),
            (lambda:self.detail.update(availability='Unsearchable'),'INVALID_ARTWORK'),
            (lambda:self.detail.update(ingestion='Blocked'),'INVALID_ARTWORK'),
            (lambda:self.detail.update(nsfwLevel=2),'INVALID_ARTWORK'),
            (lambda:self.item.update(url='https://evil.test/art.jpeg'),'INVALID_ARTWORK')):
            self.setUp(); mutate()
            with self.assertRaises(CommunityError) as caught:self.verifier.artwork(123)
            self.assertEqual(code,caught.exception.code)


if __name__=='__main__': unittest.main()

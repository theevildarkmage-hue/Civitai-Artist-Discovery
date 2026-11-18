"""Canonical follower observations expire, preserve metadata and accept legitimate decreases."""
import test_safety
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys,tempfile,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from discovery.taste import TasteStore
from discovery.followers import follower_count,follower_fresh

class Immediate(threading.Event):
    def wait(self,timeout=None):return self.is_set()

class Freshness(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='civitai-follower-freshness-');self.root=Path(self.temp.name)
        self.store=TasteStore(self.root/'discovery');self.path=self.root/'profiles.json'
        self.old=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        self.profile={'id':12,'username':'Artist','profilePicture':{'url':'original-avatar'},
                      'stats':{'followerCountAllTime':8,'unrelatedMetric':99},'customField':{'preserve':True}}
        self.path.write_text(json.dumps({'byUsername':{'artist':self.profile}}))
        self.patches=[patch.object(server,'TASTE',self.store),patch.object(server,'CREATOR_PROFILES',self.path)]
        for p in self.patches:p.start()
        server.CREATOR_REFRESH_AFTER.clear();server.PROFILE_CACHE.update(key=None,value={})
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def observe(self,count,stamp=None):
        self.store.cache_follower_profiles([{'id':12,'username':'Artist','stats':{'followerCountAllTime':count}}],stamp)
    def test_fresh_sql_count_overrides_bad_profile_everywhere(self):
        self.observe(2782)
        with patch.object(server,'SocialClient') as transport:
            result=server.enrich_creator_metadata(['ARTIST'],include_follow=False)['artist']
            transport.return_value.batch_query_optional.assert_not_called()
        decorated=server.decorate_history_artist({'username':'Artist'}, {'artist':self.profile},set())
        self.assertEqual(result['followers'],2782);self.assertFalse(result['emerging'])
        self.assertFalse(result['followersStale'])
        self.assertEqual(decorated['followers'],2782);self.assertFalse(decorated['emerging'])
        self.assertEqual(self.store.follower_counts(['Artist']),{'artist':2782})
    def test_legacy_profile_and_expired_count_refresh_without_rewriting_metadata(self):
        self.observe(2688,self.old);before=self.path.read_bytes()
        with patch.object(server,'SocialClient') as transport:
            transport.return_value.batch_query_optional.return_value=[{'id':12,'username':'Artist','stats':{'followerCountAllTime':2900}}]
            result=server.enrich_creator_metadata(['Artist'],include_follow=False)['artist']
            again=server.enrich_creator_metadata(['Artist'],include_follow=False)['artist']
            self.assertEqual(transport.return_value.batch_query_optional.call_count,1)
        self.assertEqual(result['followers'],2900);self.assertEqual(again['followers'],2900)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.store.follower_coverage(['Artist']),(1,1))
    def test_decreasing_count_and_thresholds(self):
        for count,emerging in ((5000,False),(999,True),(1000,False),(0,True)):
            self.observe(count)
            result=server._follower_fields(self.profile,self.store.follower_counts(['Artist'])['artist'])
            self.assertEqual((result['followers'],result['emerging']),(count,emerging))
    def test_invalid_or_missing_upstream_count_preserves_observation_age(self):
        self.observe(2688,self.old)
        for value in (None,True,-1,float('nan'),float('inf'),1.5,10**1000):
            self.observe(value)
        row=self.store.follower_records(['Artist'])['artist']
        self.assertEqual(row['follower_count'],2688);self.assertEqual(row['fetched_at'],self.old)
        self.assertFalse(follower_fresh(row['fetched_at']))
        self.assertIsNone(follower_count(None));self.assertFalse(server._follower_fields(None)['emerging'])
    def test_offline_keeps_better_sql_count_and_bounds_retry(self):
        self.observe(2688,self.old)
        with patch.object(server,'SocialClient') as transport:
            transport.return_value.batch_query_optional.side_effect=RuntimeError('Synthetic offline')
            for _ in range(2):
                result=server.enrich_creator_metadata(['Artist'],include_follow=False)['artist']
                self.assertEqual(result['followers'],2688);self.assertFalse(result['emerging'])
                self.assertTrue(result['followersStale'])
            self.assertEqual(transport.return_value.batch_query_optional.call_count,1)
        self.assertEqual(self.store.follower_records(['Artist'])['artist']['fetched_at'],self.old)
    def test_sweep_refreshes_expired_count_once_and_preserves_tags(self):
        self.observe(8,self.old)
        with self.store.connect() as db:db.execute("INSERT INTO archive_image_tags VALUES(42,'preserve')")
        client=unittest.mock.Mock();client.batch_query_optional.return_value=[{'id':12,'username':'Artist','stats':{'followerCountAllTime':2782}}]
        self.assertEqual(self.store.follower_coverage(['Artist']),(0,1))
        self.assertEqual(self.store.sweep_followers(client,['Artist'],Immediate()),1)
        self.assertEqual(self.store.sweep_followers(client,['Artist'],Immediate()),0)
        self.assertEqual(client.batch_query_optional.call_count,1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT tag_name FROM archive_image_tags').fetchone()[0],'preserve')
    def test_name_resolution_cannot_make_old_count_fresh(self):
        with self.store.connect() as db:db.execute('INSERT INTO creator_followers VALUES(12,NULL,2688,?)',(self.old,))
        client=unittest.mock.Mock();client.query.return_value=[12];client.batch_query_optional.return_value=[{'id':12,'username':'Artist'}]
        with patch.object(self.store,'_pause'):self.store._sync_following(client)
        row=self.store.follower_records(['Artist'])['artist']
        self.assertEqual(row['fetched_at'],self.old);self.assertEqual(row['follower_count'],2688)
    def test_old_observation_cannot_replace_newer_and_unicode_lookup_works(self):
        self.observe(2782);self.observe(8,self.old)
        self.assertEqual(self.store.follower_counts(['ARTIST'])['artist'],2782)
        self.store.cache_follower_profiles([{'id':13,'username':'Straße','stats':{'followerCountAllTime':20}}])
        self.assertEqual(self.store.follower_counts(['STRASSE'])['strasse'],20)

if __name__=='__main__':unittest.main()

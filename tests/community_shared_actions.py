"""Shared services admit only displayed artwork for this viewer and current controls.

Transport is mocked; these tests make no Civitai writes and claim no live verification.
"""
import test_safety
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(test_safety.ROOT))
from discovery.community import CommunityClient, ContentProfile, FixtureVerifier


class AdmissionTests(unittest.TestCase):
    def test_remote_feed_never_masks_revocation_with_cached_success(self):
        from discovery.community import CommunityError
        backend=Mock();backend.request.return_value={'apiVersion':1,'items':[]}
        client=CommunityClient(backend,None,lambda:'synthetic-fresh',lambda token:None)
        client.mode='supabase';client.identity_provider=lambda:42
        client.store=Mock()
        client._read('gallery',None,{'refresh':True})
        backend.request.assert_called_once_with('gallery','synthetic-fresh',{})
        client.store.get.assert_not_called()
        backend.request.side_effect=CommunityError('FORBIDDEN',403)
        with self.assertRaises(CommunityError):client._read('gallery',None,{})
        client.store.get.assert_not_called()

    def test_report_forwards_only_verified_transport_and_minimal_payload(self):
        from discovery.community import CommunityError
        backend=Mock();backend.request.return_value={'apiVersion':1,'reported':True}
        client=CommunityClient(backend,None,lambda:'synthetic-fresh-credential',lambda token:self.fail('Report fetched unrelated content preferences'))
        client.mode='supabase'
        client.request('report',{'artwork':1001,'reason':'abuse','memberId':999})
        backend.request.assert_called_once_with('report','synthetic-fresh-credential',{'artwork':1001,'reason':'abuse'})
        with self.assertRaises(CommunityError):client.request('report',{'artwork':1001,'reason':'free text'})
        client.mode='fixture'
        with self.assertRaises(CommunityError):client.request('report',{'artwork':1001,'reason':'abuse'})

    def setUp(self):
        self.verifier = FixtureVerifier(development=True)
        self.profile = ContentProfile(levels=frozenset((1,2,4,8,16)))
        self.client = CommunityClient(None, self.verifier, lambda:'fixture:1', lambda token:self.profile)
        self.client.mode = 'fixture'
        self.art = self.verifier.artworks[1001]
        self.viewer = ('isolated', 'fixture:1')
        self.rows = [{'artwork':self.art.browser()}]

    def test_admission_account_controls_removal(self):
        self.assertIsNone(self.client.presented_artwork(1001, self.viewer))
        self.client.remember_presented(self.rows, self.viewer)
        self.assertEqual(self.art, self.client.presented_artwork(1001, self.viewer))
        self.assertIsNone(self.client.presented_artwork(1001, ('other-account', 'fixture:1')))
        self.assertIsNone(self.client.presented_artwork(1002, self.viewer))
        self.profile = ContentProfile(images=frozenset((1001,)))
        self.assertIsNone(self.client.presented_artwork(1001, self.viewer))
        self.assertIsNone(self.client.presented_creator(self.art.username, self.viewer))
        self.profile = ContentProfile()
        self.verifier.artworks.pop(1001)
        self.assertIsNone(self.client.presented_artwork(1001, self.viewer))

    def test_bounded_admission(self):
        self.client.remember_presented([{'artwork':{'id':i,'username':'Synthetic'}} for i in range(1,2100)], self.viewer)
        self.assertEqual(2000, len(self.client.presented))
        self.assertNotIn((self.viewer, 1), self.client.presented)

    def test_reaction_handler_uses_live_counts_and_rejects_unknown(self):
        with tempfile.TemporaryDirectory(prefix='data-test-shared-actions-') as temporary:
            os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
            import server
            self.client.remember_presented(self.rows, self.viewer)
            handler = server.Handler.__new__(server.Handler)
            handler.headers = {}; handler.require_community_session = lambda:None
            handler.community_viewer = lambda token:self.viewer
            handler.json_response = Mock()
            transport = Mock()
            transport.query.return_value = {'stats':{'likeCountAllTime':22},'reactions':[]}
            archive = Mock(); archive.has_image.return_value = False
            cache = Mock(); cache.load.return_value = {'items':[]}
            with patch.multiple(server, COMMUNITY=self.client, HISTORY=archive, TIME_MACHINE=archive,
                                CACHE=cache, SocialClient=lambda:transport):
                with self.assertRaises(ValueError):
                    handler.handle_reaction({'imageId':1002,'reaction':'Like','active':True})
                transport.query.assert_not_called()
                handler.handle_reaction({'imageId':1001,'reaction':'Like','active':True})
                response = handler.json_response.call_args.args[0]
                self.assertEqual(23, response['stats']['likeCount'])
                self.assertTrue(response['changed'])
                transport.query.return_value['reactions'] = ['Like']
                handler.handle_reaction({'imageId':1001,'reaction':'Like','active':True})
                self.assertFalse(handler.json_response.call_args.args[0]['changed'])
                self.assertEqual(1, transport.mutate.call_count)
                archive.update_stats.assert_not_called(); cache.save.assert_not_called()

    def test_details_use_existing_archive_without_cloud_cache(self):
        with tempfile.TemporaryDirectory(prefix='data-test-shared-detail-') as temporary:
            os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
            import server
            archive = Mock(); archive.detail.return_value = {'prompt':'Synthetic prompt','width':640,'private':'omit'}
            with patch.object(server, 'image_store', return_value=archive):
                self.assertEqual({'prompt':'Synthetic prompt','width':640},
                    server.community_detail_metadata(self.client, self.art.browser()))
            archive.detail.assert_called_once_with(1001)

    def test_uncached_details_fetch_once_and_stay_local(self):
        with tempfile.TemporaryDirectory(prefix='data-test-shared-detail-') as temporary:
            os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
            import server
            from discovery.cache import MetadataCache
            self.client.store = MetadataCache(Path(temporary)/'metadata.sqlite3')
            archive = Mock()
            raw = {'id':1001,'username':self.art.username,'meta':{'prompt':'Synthetic metadata'},'width':640}
            archive._request.return_value = ({'items':[raw]}, None)
            with patch.object(server, 'image_store', return_value=None), patch.object(server, 'HISTORY', archive), \
                    patch('discovery.community_dev.test_identity', return_value=None):
                first = server.community_detail_metadata(self.client, self.art.browser())
                self.assertEqual('Synthetic metadata', first['prompt'])
                self.assertEqual(first, server.community_detail_metadata(self.client, self.art.browser()))
                self.assertEqual(1, archive._request.call_count)
                self.assertEqual(640, self.client.store.get('fixture:detail:1001')['value']['width'])
            archive._upsert_normalized.assert_not_called()

    def test_details_preserve_nested_generation_metadata(self):
        from discovery.civitai import normalize
        raw={'id':1001,'username':self.art.username,'baseModel':'Pony','width':640,'height':800,
             'meta':{'id':1001,'meta':{'prompt':'Synthetic complete prompt',
                     'negativePrompt':'Synthetic negative',
                     'resources':[{'name':'Synthetic model','type':'checkpoint'}]}}}
        detail=normalize(raw)
        self.assertEqual('Synthetic complete prompt',detail['prompt'])
        self.assertEqual('Synthetic negative',detail['negativePrompt'])
        self.assertEqual('Pony',detail['baseModel'])
        self.assertEqual('Synthetic model',detail['resources'][0]['name'])
        missing=normalize({'id':1001})
        self.assertEqual('',missing['prompt'])
        self.assertEqual([],missing['resources'])

    def test_detail_failures_do_not_cache_missing_or_wrong_owner(self):
        from discovery.community import CommunityError
        from discovery.cache import MetadataCache
        with tempfile.TemporaryDirectory(prefix='data-test-shared-detail-') as temporary:
            os.environ['CIVITAI_HISTORY_DATA_DIR']=temporary
            import server
            self.client.store=MetadataCache(Path(temporary)/'metadata.sqlite3')
            archive=Mock()
            with patch.object(server,'image_store',return_value=None),patch.object(server,'HISTORY',archive), \
                    patch('discovery.community_dev.test_identity',return_value=None):
                for rows in ([],[{'id':1001,'username':'WrongOwner'}]):
                    archive._request.return_value=({'items':rows},None)
                    with self.assertRaises(CommunityError):
                        server.community_detail_metadata(self.client,self.art.browser())
                    self.assertIsNone(self.client.store.get('fixture:detail:1001'))
                archive._request.side_effect=TimeoutError('Synthetic outage')
                with self.assertRaises(TimeoutError):
                    server.community_detail_metadata(self.client,self.art.browser())
                self.assertIsNone(self.client.store.get('fixture:detail:1001'))


if __name__ == '__main__': unittest.main()

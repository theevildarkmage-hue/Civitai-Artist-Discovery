"""Legacy control mirrors remain readable while source metadata is recovered."""
import test_safety

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discovery.taste import HIDDEN_PREFERENCES_RETRY_SECONDS, TasteStore
from discovery.settings import AppSettings


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='civitai-control-recovery-')
        self.addCleanup(self.temporary.cleanup)
        self.store = TasteStore(Path(self.temporary.name))
        with self.store.connect() as db:
            db.executemany('INSERT INTO hidden_images VALUES(?)', [(101,), (102,)])
            self.store._set_state(db, 'hidden_imported_at', '2026-10-01T00:00:00Z')

    def join_recovery(self):
        self.store._hidden_preferences_thread.join(timeout=5)
        self.assertFalse(self.store._hidden_preferences_thread.is_alive())

    def test_offline_reads_keep_mirror_and_bound_retries(self):
        entered, release = threading.Event(), threading.Event()

        def unavailable(*_args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test did not release the simulated request')
            raise TimeoutError('Synthetic offline service')

        with patch('discovery.taste.time.monotonic', return_value=100) as clock, \
                patch('discovery.taste.auth_status', return_value={'id': 7}), \
                patch('discovery.taste.SocialClient.query', side_effect=unavailable) as query:
            try:
                self.assertEqual({101, 102}, self.store.hidden_image_ids())
                self.assertTrue(entered.wait(5))
                # All concurrent readers proceed while the upstream request is blocked.
                with ThreadPoolExecutor(max_workers=4) as pool:
                    reads = [pool.submit(self.store.hidden_image_ids, include_images=False)
                             for _ in range(4)]
                    for read in reads:
                        self.assertEqual({101, 102}, read.result(timeout=2))
                self.assertEqual(2, self.store.hidden_summary()['images'])
                self.assertEqual(1, query.call_count)
            finally:
                release.set()
                self.join_recovery()
            self.assertEqual({101, 102}, self.store.hidden_image_ids())
            self.assertEqual(1, query.call_count)
            clock.return_value = 100 + HIDDEN_PREFERENCES_RETRY_SECONDS
            self.store.hidden_image_ids()
            self.join_recovery()
            self.assertEqual(2, query.call_count)
            with self.store.connect() as db:
                self.assertIsNone(self.store._state(db, 'hidden_image_preferences'))
                self.assertEqual('2026-10-01T00:00:00Z', self.store._state(db, 'hidden_imported_at'))

    def test_successful_recovery_restores_switch_semantics_and_stops_refreshing(self):
        controls = {'hiddenUsers': [], 'blockedUsers': [], 'blockedByUsers': [],
                    'hiddenTags': [{'id': 11, 'name': 'topic', 'hidden': True}],
                    'hiddenImages': [101], 'hiddenImagesImplicit': [{'id': 102, 'tagId': 11}]}
        with patch('discovery.taste.auth_status', return_value={'id': 7}), \
                patch('discovery.taste.SocialClient.query', return_value=controls) as query:
            self.store.ensure_hidden_preferences_current()
            self.join_recovery()
            self.assertEqual({101, 102}, self.store.hidden_image_ids())
            self.assertEqual({102}, self.store.hidden_image_ids(include_images=False))
            self.assertEqual({101}, self.store.hidden_image_ids(include_tags=False))
            self.assertEqual(set(), self.store.hidden_image_ids(include_images=False, include_tags=False))
            self.assertEqual(1, query.call_count)

    def test_community_keeps_legacy_exclusions_while_offline(self):
        import server
        settings = AppSettings(Path(self.temporary.name) / 'settings.json')
        settings.update(apply_tag_filters_value=False, apply_image_filters_value=False)
        with patch.object(server, 'TASTE', self.store), patch.object(server, 'SETTINGS', settings), \
                patch('discovery.taste.auth_status', return_value={'id': 7}), \
                patch('discovery.taste.SocialClient.query', side_effect=TimeoutError) as query:
            profile = server.community_content_profile(None)
            self.join_recovery()
            self.assertEqual({101, 102}, profile.excluded_image_ids())
            self.assertEqual({101, 102}, server.community_content_profile(None).excluded_image_ids())
            self.assertEqual(1, query.call_count)

    def test_account_change_during_recovery_keeps_previous_mirror(self):
        account = [7]
        controls = {'hiddenUsers': [], 'blockedUsers': [], 'blockedByUsers': [],
                    'hiddenTags': [], 'hiddenImages': []}

        def changed_account(*_args):
            account[0] = 8
            return controls

        with patch('discovery.taste.auth_status', side_effect=lambda: {'id': account[0]}), \
                patch('discovery.taste.SocialClient.query', side_effect=changed_account):
            self.store.ensure_hidden_preferences_current()
            self.join_recovery()
            self.assertEqual({101, 102}, self.store.hidden_image_ids())
            with self.store.connect() as db:
                self.assertIsNone(self.store._state(db, 'hidden_image_preferences'))


if __name__ == '__main__':
    unittest.main()

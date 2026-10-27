"""Independent local viewing switches preserve imported rules and explicit hides."""
import test_safety
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from discovery.community import ContentProfile, FixtureVerifier
from discovery.settings import AppSettings
from discovery.taste import TasteStore


class FilterToggleTests(unittest.TestCase):
    def test_thumbnail_gate_uses_switches_and_keeps_explicit_hides(self):
        import server
        from discovery.history import HistoryArchive
        with tempfile.TemporaryDirectory(prefix='cad3-filter-thumbnail-') as temporary:
            root = Path(temporary)
            settings = AppSettings(root / 'settings.json')
            store = TasteStore(root / 'taste')
            archive = HistoryArchive(root / 'history')
            archive._upsert_normalized([{'id': 1, 'username': 'HiddenArtist',
                'type': 'image', 'browsingLevel': 1, 'nsfwLevel': 'None',
                'createdAt': '2026-10-20T13:00:00Z', 'stats': {},
                'url': 'https://image.civitai.com/synthetic/1.jpeg'}], forced_date='2026-10-20')
            with store.connect() as db:
                db.execute("INSERT INTO hidden_creators VALUES(101,'hiddenartist','blocked')")
                db.execute("INSERT INTO hidden_tags VALUES(11,'landscape')")
                db.execute("INSERT INTO archive_image_tags VALUES(1,'landscape')")
            cache = Mock()
            cache.get.return_value = (b'synthetic-thumbnail', 'image/png')
            with patch.multiple(server, SETTINGS=settings, TASTE=store, HISTORY=archive,
                                PERSONAL_THUMBNAILS=cache, auth_status=lambda: {'id': 42}):
                def read():
                    handler = server.Handler.__new__(server.Handler)
                    handler.path = '/api/history/thumbnail/1'
                    handler.headers = {}
                    handler.host_allowed = lambda: True
                    handler.json_response = Mock()
                    handler.send_response = Mock()
                    handler.send_header = Mock()
                    handler.end_headers = Mock()
                    handler.wfile = io.BytesIO()
                    handler.do_GET()
                    return handler
                self.assertEqual(read().json_response.call_args.args[1], 403)
                settings.update(apply_tag_filters_value=False)
                self.assertEqual(read().json_response.call_args.args[1], 403)
                cache.get.assert_not_called()
                settings.update(apply_account_filters_value=False)
                allowed = read()
                allowed.send_response.assert_called_once_with(200)
                self.assertEqual(allowed.wfile.getvalue(), b'synthetic-thumbnail')
                with store.connect() as db:
                    db.execute('INSERT INTO hidden_images VALUES(1)')
                self.assertEqual(read().json_response.call_args.args[1], 403)

    def test_defaults_validation_and_persistence(self):
        with tempfile.TemporaryDirectory(prefix='cad3-filter-toggles-') as temporary:
            path = Path(temporary) / 'settings.json'
            settings = AppSettings(path)
            for raw in ({}, {'applyTagFilters': 'false', 'applyAccountFilters': 0}):
                path.write_text(json.dumps(raw))
                self.assertTrue(settings.load()['applyTagFilters'])
                self.assertTrue(settings.load()['applyAccountFilters'])
            settings.update(apply_tag_filters_value=False)
            self.assertFalse(AppSettings(path).load()['applyTagFilters'])
            self.assertTrue(settings.load()['applyAccountFilters'])
            settings.update(apply_account_filters_value=False, browsing_levels_value=[1])
            settings.update(dim_seen_cards_value=False)
            self.assertFalse(settings.load()['applyTagFilters'])
            self.assertFalse(settings.load()['applyAccountFilters'])
            previous = path.read_bytes()
            for change in ({'apply_tag_filters_value': 'false'}, {'apply_account_filters_value': 0}):
                with self.assertRaises(ValueError):
                    settings.update(**change)
                self.assertEqual(path.read_bytes(), previous)

    def test_tag_pause_preserves_explicit_hides_and_imported_tags(self):
        with tempfile.TemporaryDirectory(prefix='cad3-filter-tags-') as temporary:
            store = TasteStore(Path(temporary))
            with store.connect() as db:
                db.execute("INSERT INTO hidden_tags VALUES(11,'landscape')")
                db.execute('INSERT INTO hidden_images VALUES(2)')
                db.execute("INSERT INTO archive_image_tags VALUES(1,'landscape')")
            self.assertEqual(store.hidden_image_ids(), {1, 2})
            self.assertEqual(store.hidden_image_ids(include_tags=False), {2})
            self.assertEqual(store.hidden_tag_names(), {'landscape'})
            self.assertEqual(store.hidden_image_ids(), {1, 2})

    def test_community_switches_are_independent(self):
        art = FixtureVerifier(development=True).artworks[1001]
        profile = ContentProfile(tags=frozenset(art.tags), creators=frozenset((art.creator_id,)))
        self.assertEqual(set(profile.exclusions(art)), {'hiddenTag', 'hiddenCreator'})
        self.assertEqual(profile.exclusions(replace(art, owner_verified=False)),
                         ['hiddenCreator', 'hiddenTag', 'incompleteMetadata'])
        self.assertEqual(replace(profile, apply_tag_filters=False).exclusions(art), ['hiddenCreator'])
        self.assertEqual(replace(profile, apply_account_filters=False).exclusions(art), ['hiddenTag'])
        paused = replace(profile, apply_tag_filters=False, apply_account_filters=False)
        self.assertTrue(paused.allows(art))
        self.assertFalse(replace(paused, images=frozenset((art.id,))).allows(art))
        self.assertFalse(replace(paused, levels=frozenset((16,))).allows(art))
        blocked = replace(paused, apply_account_filters=True,
                          blocked_creators=frozenset((art.creator_id,)))
        self.assertIn('blockedUser', blocked.exclusions(art))
        unknown = replace(art, tags_known=False)
        self.assertIn('incompleteMetadata', profile.exclusions(unknown))
        self.assertTrue(paused.allows(unknown))


if __name__ == '__main__':
    unittest.main()

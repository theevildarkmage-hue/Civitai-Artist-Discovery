"""Owner SQL tooling boundaries; actual roles are exercised by native tests."""
import importlib.util
import json
from pathlib import Path
import unittest
import test_safety

spec = importlib.util.spec_from_file_location('owner_admin', test_safety.ROOT/'scripts/community-owner-admin.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OwnerAdminTests(unittest.TestCase):
    def test_untrusted_text_is_hex_encoded(self):
        attack = "';commit;select pg_sleep(99);--\\$tag$\n"
        query = module.statement('block', 123, attack, rollback=True)
        self.assertNotIn(attack, query)
        self.assertIn(attack.encode().hex(), query)
        self.assertTrue(query.endswith('rollback;'))
        self.assertNotIn('commit;', query)

    def test_action_and_target_allowlist(self):
        for action, target, reason in [('drop', 1, 'test'), ('block', -1, 'test'), ('block', True, 'test'), ('block', 1, 'x')]:
            with self.assertRaises(ValueError):
                module.statement(action, target, reason)

    def test_management_transport_and_sanitized_errors(self):
        import urllib.error
        class Fake:
            def open(self, request, timeout):
                self.request = request
                raise urllib.error.HTTPError(request.full_url, 403, 'SECRET_SENTINEL', {}, None)
        manager = module.Management('TOKEN_SENTINEL')
        fake = Fake();manager.opener = fake
        with self.assertRaises(RuntimeError) as caught:
            manager.request('/database/query', 'select 1', read_only=False)
        self.assertNotIn('SENTINEL', str(caught.exception))
        self.assertIn('403', str(caught.exception))
        self.assertEqual(json.loads(fake.request.data), {'query': 'select 1', 'read_only': False})
        with self.assertRaises(ValueError):manager.request('/functions')


if __name__ == '__main__':unittest.main()

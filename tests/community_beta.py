"""Automatic Community connection and independent hosted security controls."""
import test_safety
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from discovery import community_beta as beta
from discovery.community import CommunityError

class BetaControls(unittest.TestCase):
    def test_development_supabase_still_requires_server_policy(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(beta,'production',return_value=False):
            with patch.dict(os.environ,{'CAD_COMMUNITY_MODE':'supabase'}):
                self.assertTrue(beta.configuration(folder)['serverPolicy'])
            for mode in ('local','fixture'):
                with patch.dict(os.environ,{'CAD_COMMUNITY_MODE':mode}):
                    self.assertFalse(beta.configuration(folder)['serverPolicy'])

    def test_fresh_and_legacy_profiles_connect_without_writing_preferences(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(beta,'production',return_value=True):
            root=Path(folder);(root/'settings.json').write_text('{"browsingLevels":[1,2]}')
            original=(root/'settings.json').read_bytes()
            for legacy in (None,'{"optedIn":false}','{"optedIn":true}','invalid JSON'):
                path=root/'community-beta.json'
                if legacy is not None:path.write_text(legacy)
                config=beta.configuration(root)
                self.assertTrue(config['enabled']);self.assertTrue(config['serverPolicy'])
                self.assertNotIn('optedIn',config)
                beta.require_enabled(root)
                self.assertEqual(original,(root/'settings.json').read_bytes())
                self.assertEqual(path.read_text() if path.exists() else None,legacy)
            with patch.object(beta,'BETA_ENDPOINT',None):
                self.assertFalse(beta.configuration(root)['enabled'])
                with self.assertRaises(CommunityError) as caught:beta.require_enabled(root)
                self.assertEqual(caught.exception.code,'UNAVAILABLE')

    def test_frozen_cannot_enable_development_trust_with_environment(self):
        with patch.object(sys,'frozen',True,create=True),patch.dict(os.environ,{'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_TESTING':'1','CAD_COMMUNITY_MODE':'fixture'}):
            from discovery.community_security import fixture_enabled,test_identity
            self.assertTrue(beta.production());self.assertFalse(fixture_enabled());self.assertIsNone(test_identity(Path.cwd()))

subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_beta_edge.mjs')],check=True)
subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_credentials_edge.mjs')],check=True)
if __name__=='__main__':unittest.main()

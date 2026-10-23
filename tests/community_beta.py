"""Default-off rollout preferences and independent server kill/rating controls."""
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

    def test_new_and_upgraded_profiles_are_off_and_preferences_survive(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(beta,'production',return_value=True):
            root=Path(folder);(root/'settings.json').write_text('{"browsingLevels":[1,2]}')
            original=(root/'settings.json').read_bytes()
            self.assertFalse(beta.configuration(root)['enabled'])
            with patch.object(beta,'BETA_ENDPOINT',None):
                with self.assertRaises(ValueError):beta.opt_in(root,True)
            with patch.object(beta,'BETA_ENDPOINT','https://reviewed.supabase.co/functions/v1/community-v1'):
                self.assertTrue(beta.opt_in(root,True)['enabled'])
                self.assertTrue(beta.configuration(root)['optedIn'])
                self.assertFalse(beta.opt_in(root,False)['enabled'])
            self.assertEqual(original,(root/'settings.json').read_bytes())
            with self.assertRaises(CommunityError):beta.require_enabled(root)

    def test_frozen_cannot_enable_development_trust_with_environment(self):
        with patch.object(sys,'frozen',True,create=True),patch.dict(os.environ,{'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_TESTING':'1','CAD_COMMUNITY_MODE':'fixture'}):
            from discovery.community_security import fixture_enabled,test_identity
            self.assertTrue(beta.production());self.assertFalse(fixture_enabled());self.assertIsNone(test_identity(Path.cwd()))

subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_beta_edge.mjs')],check=True)
subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_credentials_edge.mjs')],check=True)
if __name__=='__main__':unittest.main()

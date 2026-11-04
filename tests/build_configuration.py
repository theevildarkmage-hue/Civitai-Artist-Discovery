"""Build-time endpoint isolation and channel identity; no network or real profiles."""
import test_safety
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery import build_config,community_beta,cloud_identity,registry

class BuildConfiguration(unittest.TestCase):
    def test_clients_share_reviewed_endpoint(self):
        self.assertEqual(community_beta.BETA_ENDPOINT,build_config.ENDPOINT)
        self.assertEqual(cloud_identity.ENDPOINT,build_config.ENDPOINT)
        self.assertEqual(registry.ENDPOINT,build_config.ENDPOINT)
        with patch.dict(os.environ,{'CAD_COMMUNITY_URL':'https://attacker.invalid','SUPABASE_URL':'https://attacker.invalid'}):
            self.assertEqual(build_config.load(),build_config.DEFAULT)

    def test_explicit_channel_environment_boundaries(self):
        base=build_config.DEFAULT
        stable={**base,'channel':'stable','version':'3.0.0','environment':'production','projectRef':'abcdefghijklmnopqrst'}
        self.assertEqual(build_config.validate(stable),stable)
        preview={**stable,'environment':'development','projectRef':build_config.DEVELOPMENT_PROJECT,'preview':True}
        self.assertEqual(build_config.validate(preview),preview)
        invalid=[{**stable,'projectRef':build_config.DEVELOPMENT_PROJECT},
                 {**base,'environment':'production','projectRef':'abcdefghijklmnopqrst'},
                 {**preview,'preview':False}, {**base,'version':'3.0.0'},
                 {**stable,'version':'3.0.0-beta.14'}, {**base,'projectRef':'https://attacker.invalid'},
                 {**base,'schemaVersion':True},{**base,'preview':'false'},
                 {**stable,'version':'3.00.0'},{**base,'version':'3.01.0-beta.14'},
                 {**base,'secret':'synthetic-canary'},{**base,'projectRef':'x'*21}]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):build_config.validate(value)

    def test_frozen_configuration_is_required_and_validated(self):
        with tempfile.TemporaryDirectory(prefix='cad3-build-config-') as name:
            root=Path(name)
            with patch.object(sys,'frozen',True,create=True),patch.object(build_config,'__file__',str(root/'build_config.py')):
                with self.assertRaises(RuntimeError):build_config.load()
                path=root/'build-configuration.json'
                path.write_text(json.dumps(build_config.DEFAULT),encoding='utf-8')
                self.assertEqual(build_config.load(),build_config.DEFAULT)
                path.write_text('{"endpoint":"https://attacker.invalid"}',encoding='utf-8')
                with self.assertRaises(ValueError):build_config.load()

if __name__=='__main__':unittest.main()

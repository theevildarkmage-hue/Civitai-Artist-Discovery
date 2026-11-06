"""Accidental/forced runtime staging is rejected without printing credential values."""
import test_safety
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('source_gate',test_safety.ROOT/'scripts/check-source.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)


class SourceHygiene(unittest.TestCase):
    def test_sensitive_paths_and_reviewed_sources(self):
        for name in ('data-test-community/profile.sqlite3','.venv-3.0/config.py','reports/output.json',
                     'discovery/oauth_tokens.dpapi','supabase/.env','supabase/private.key','tests/fixtures/private.png'):
            self.assertTrue(gate.forbidden(name),name)
        for name in ('tests/data_location.py','discovery/cache.py','supabase/.env.example','tests/fixtures/recovery.mp4'):
            self.assertFalse(gate.forbidden(name),name)

    def test_index_content_is_checked_even_when_ignore_is_forced(self):
        with tempfile.TemporaryDirectory(prefix='cad3-source-gate-') as temporary:
            root=Path(temporary)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'data-test-private').mkdir();(root/'data-test-private/profile.sqlite3').write_bytes(b'synthetic')
            (root/'example.py').write_text('credential = '+repr('ghp_'+'x'*30))
            (root/'private-path.md').write_text('C:'+'\\Users\\ExampleUser\\app')
            subprocess.run(['git','-C',str(root),'add','-f','.'],check=True,capture_output=True)
            with patch.object(gate,'ROOT',root):names,findings=gate.audit(staged=True)
            self.assertEqual(len(findings),3)
            self.assertNotIn('ghp_'+'x'*30,' '.join(findings))


if __name__=='__main__':unittest.main()

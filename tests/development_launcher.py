"""Launcher rejects unrelated/ambiguous listeners and never terminates processes."""
import test_safety
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('dev_launcher',test_safety.ROOT/'scripts/dev-launcher.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class DevelopmentLauncher(unittest.TestCase):
    def test_exact_source_ownership(self):
        root=test_safety.ROOT
        self.assertTrue(launcher.owns_process({'ExecutablePath':'C:\\Python314\\python.exe','CommandLine':f'python "{root / "server.py"}" --port 8907'}))
        for path in (root.parent/'Civitai Desktop Client/server.py',Path(str(root/'server.py')+'.evil')):
            self.assertFalse(launcher.owns_process({'ExecutablePath':'C:\\Python314\\python.exe','CommandLine':f'python "{path}"'}))

    def test_reuse_requires_matching_pid_source_profile_and_version(self):
        with tempfile.TemporaryDirectory(prefix='cad3-launcher-') as temporary:
            root=Path(temporary);(root/'running-instance.json').write_text(json.dumps({'pid':123,'url':'http://127.0.0.1:8907'}))
            listeners=[{'LocalAddress':'127.0.0.1','OwningProcess':123}]
            good={'ExecutablePath':'C:\\Python314\\python.exe','CommandLine':f'python "{test_safety.ROOT / "server.py"}"'}
            with patch.object(launcher,'powershell',return_value=good),patch.object(launcher,'read_json',return_value={'version':'3.0.0-dev'}):
                self.assertEqual(launcher.owned_instance(root,8907,listeners=listeners)['pid'],123)
                with self.assertRaises(RuntimeError):launcher.owned_instance(root,8907,listeners=[{'LocalAddress':'127.0.0.1','OwningProcess':999}])
            with patch.object(launcher,'powershell',return_value={'ExecutablePath':'python.exe','CommandLine':'python unrelated.py'}):
                with self.assertRaises(RuntimeError):launcher.owned_instance(root,8907,listeners=listeners)
            with patch.object(launcher,'powershell',return_value=good),patch.object(launcher,'read_json',side_effect=[
                    {'version':'3.0.0-dev','developmentDiagnostics':True},
                    {'sourceDirectory':str(test_safety.ROOT),'dataDirectory':str(root/'other'),'processId':123}]):
                with self.assertRaisesRegex(RuntimeError,'profile identity'):launcher.owned_instance(root,8907,listeners=listeners)
            self.assertIsNone(launcher.owned_instance(root,8907,listeners=[]))


if __name__=='__main__':unittest.main()

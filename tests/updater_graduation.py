"""Verified beta-to-stable identities, executable migration and interrupted recovery."""
import test_safety
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from discovery import updater


def release(version='3.0.0', *, commit='a' * 40):
    channel = 'beta' if '-beta.' in version else 'stable'
    app = updater.APPLICATIONS[channel]
    name = f'{app}-{version}.zip'
    return {'tag_name': 'v' + version, 'prerelease': channel == 'beta',
            'target_commitish': commit, 'assets': [{'name': name, 'state': 'uploaded',
            'size': 1, 'digest': 'sha256:' + 'b' * 64,
            'browser_download_url': f'https://github.com/{updater.REPOSITORY}/releases/download/v{version}/{name}'}]}


class GraduationTests(unittest.TestCase):
    def test_selection_stable_is_newer_but_never_admits_beta_to_stable(self):
        stable, beta = release(), release('3.0.0-beta.15')
        selected = updater.select_release([beta, stable], '3.0.0-beta.14')
        self.assertEqual((selected['channel'], selected['applicationId']), ('stable', 'CivitaiArtistDiscovery'))
        self.assertIsNotNone(updater.select_release([beta], '3.0.0-beta.13'))
        self.assertIsNone(updater.select_release([beta], '3.0.0'))
        self.assertIsNone(updater.select_release([beta], '2.2.1'))
        self.assertIsNotNone(updater.select_release([stable], '2.2.1'))

    def test_stable_identity_ref_and_metadata_fail_closed(self):
        for change in ({'prerelease': True}, {'target_commitish': 'main'},
                       {'tag_name': '3.0.0'}, {'draft': True}):
            value = release(); value.update(change)
            self.assertIsNone(updater.select_release([value], '3.0.0-beta.14'))
        selected = updater.select_release([release()], '3.0.0-beta.14')
        with patch.object(updater, '_open_json', return_value={'object': {'type': 'commit', 'sha': 'a' * 40}}):
            updater.verify_beta_ref(selected)
        with patch.object(updater, '_open_json', return_value={'object': {'type': 'commit', 'sha': 'c' * 40}}):
            with self.assertRaises(RuntimeError): updater.verify_beta_ref(selected)

    def test_stable_cache_accepts_verified_graduation_only(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-cache-') as folder:
            root = Path(folder)
            manager = updater.UpdateManager(root/'data', root, '3.0.0-beta.14', executable=root/'CivitaiDiscoveryBeta.exe', frozen=True)
            selected = updater.select_release([release()], manager.current_version)
            for key, value in [('applicationId', 'CivitaiDiscoveryBeta'), ('channel', 'beta'),
                               ('version', 'v3.0.0'),
                               ('sourceCommit', 'main'), ('assetName', '../escape.zip'),
                               ('assetUrl', 'https://github.com/other/repository/releases/download/escape.zip')]:
                invalid = dict(selected); invalid[key] = value
                updater._write_json(manager.cache_path, {'release': invalid})
                manager._load_cache(); self.assertIsNone(manager.release, key)
            updater._write_json(manager.cache_path, {'release': selected})
            manager._load_cache(); self.assertEqual(manager.release, selected)
            manager.current_version = '3.0.0'
            updater._write_json(manager.cache_path, {'release': updater.select_release([release('3.1.0-beta.1')], '3.0.0-beta.14')})
            manager._load_cache(); self.assertIsNone(manager.release)

    def package(self, root, *, identity_change=None, version='3.0.0'):
        value = release(version)
        channel = 'beta' if '-beta.' in version else 'stable'
        app = updater.APPLICATIONS[channel]
        identity = {'applicationId': app, 'channel': channel, 'version': version,
                    'tag': 'v' + version, 'sourceCommit': 'a' * 40,
                    'sourceDirty': False, 'preview': False}
        identity.update(identity_change or {})
        archive = root/'candidate.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr(app+'/'+app+'.exe', b'synthetic-new-executable')
            package.writestr(app+'/_internal/new.dll', b'new-runtime')
            package.writestr(app+'/release-identity.json', json.dumps(identity))
        data = archive.read_bytes()
        value['assets'][0].update(size=len(data), digest='sha256:'+hashlib.sha256(data).hexdigest())
        manager = updater.UpdateManager(root/'data', root, '3.0.0-beta.14', executable=root/'CivitaiDiscoveryBeta.exe', frozen=True)
        selected = updater.select_release([value], manager.current_version)
        def download(url, target, size, progress):
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            progress(size, size); return hashlib.sha256(data).hexdigest()
        with patch.object(updater, '_download', download), patch.object(updater, '_open_json', return_value={'object': {'type': 'commit', 'sha': 'a'*40}}):
            manager._download_worker(selected)
        manager.release = selected
        return manager

    def test_actual_staging_rejects_wrong_or_preview_stable_identity(self):
        for change in ({'applicationId':'CivitaiDiscoveryBeta'}, {'preview':True},
                       {'sourceDirty':True}, {'sourceCommit':'c'*40}, {'channel':'beta'}):
            with tempfile.TemporaryDirectory(prefix='cad3-graduation-identity-') as folder:
                manager = self.package(Path(folder), identity_change=change)
                self.assertEqual(manager.job['phase'], 'error', change)
                self.assertIn('identity', manager.job['error'])

    def install_fixture(self, root):
        (root/'data').mkdir(exist_ok=True)
        (root/'data/profile.json').write_text('private portable fixture')
        (root/'CivitaiDiscoveryBeta.exe').write_bytes(b'old-beta')
        (root/'_internal').mkdir(exist_ok=True)
        (root/'_internal/old.dll').write_bytes(b'old-runtime')
        manager = self.package(root)
        self.assertEqual(manager.job['phase'], 'ready', manager.job)
        command = manager.helper_command()
        self.assertEqual(Path(command[0]).name, 'CivitaiArtistDiscovery.exe')
        return manager, Path(command[-1])

    def test_graduation_retires_beta_and_relaunches_stable_preserving_profile(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-install-') as folder:
            root = Path(folder); manager, config = self.install_fixture(root)
            with patch.object(updater.subprocess, 'Popen') as launch:
                result = updater.apply_staged_update(config, wait_for_process=lambda pid: None)
            self.assertEqual(result['state'], 'installed')
            self.assertEqual(Path(launch.call_args.args[0][0]), root/'CivitaiArtistDiscovery.exe')
            self.assertFalse((root/'CivitaiDiscoveryBeta.exe').exists())
            self.assertEqual((root/'data/update/backup/3.0.0-beta.14/CivitaiDiscoveryBeta.exe').read_bytes(), b'old-beta')
            self.assertEqual((root/'data/profile.json').read_text(), 'private portable fixture')
            self.assertFalse((root/'data/update/apply-journal.json').exists())

    def test_failed_graduation_restores_old_executable_runtime_and_profile(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-rollback-') as folder:
            root = Path(folder); manager, config = self.install_fixture(root)
            original = updater._copy_entry
            def fail(source, target):
                if source.name == '_internal': raise OSError('controlled copy failure')
                return original(source, target)
            with patch.object(updater, '_copy_entry', fail):
                with self.assertRaises(OSError): updater.apply_staged_update(config, launch=False, wait_for_process=lambda pid: None)
            self.assertEqual((root/'CivitaiDiscoveryBeta.exe').read_bytes(), b'old-beta')
            self.assertEqual((root/'_internal/old.dll').read_bytes(), b'old-runtime')
            self.assertFalse((root/'CivitaiArtistDiscovery.exe').exists())
            self.assertEqual((root/'data/profile.json').read_text(), 'private portable fixture')

    def test_failed_digest_and_interrupted_download_cannot_become_ready(self):
        for failure in ('digest', 'interrupted'):
            with tempfile.TemporaryDirectory(prefix='cad3-graduation-download-') as folder:
                root = Path(folder)
                manager = updater.UpdateManager(root/'data', root, '3.0.0-beta.14', executable=root/'CivitaiDiscoveryBeta.exe', frozen=True)
                selected = updater.select_release([release()], manager.current_version)
                def download(url, target, size, progress):
                    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'partial')
                    if failure=='interrupted': raise OSError('controlled download interruption')
                    return 'c'*64
                with patch.object(updater, '_download', download), patch.object(updater, 'verify_beta_ref'):
                    manager._download_worker(selected)
                self.assertEqual(manager.job['phase'], 'error')
                self.assertFalse(list(manager.root.glob('*.part')))
                self.assertFalse((manager.root/'staged').exists())

    def test_killed_helper_recovers_before_reapplying(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-interrupted-') as folder:
            root = Path(folder); manager, config = self.install_fixture(root)
            value=json.loads(config.read_text()); value['parentPid']=0; updater._write_json(config,value)
            marker=root/'copy-reached'
            code="""import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from discovery import updater
original=updater._copy_entry
def copy(source,target):
 original(source,target)
 if source.name=='_internal':
  Path(sys.argv[3]).write_text('ready')
  while True:time.sleep(.1)
updater._copy_entry=copy
updater.apply_staged_update(Path(sys.argv[2]),launch=False)
"""
            process=subprocess.Popen([sys.executable,'-c',code,str(ROOT),str(config),str(marker)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+15
                while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue(marker.exists(), 'owned helper did not reach interrupted copy')
                process.kill();process.wait(timeout=5)
                self.assertTrue((root/'data/update/apply-journal.json').exists())
                result=updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
                self.assertEqual(result['state'],'installed')
                self.assertFalse((root/'CivitaiDiscoveryBeta.exe').exists())
                self.assertEqual((root/'CivitaiArtistDiscovery.exe').read_bytes(),b'synthetic-new-executable')
                self.assertEqual((root/'data/profile.json').read_text(),'private portable fixture')
                self.assertFalse((root/'data/update/apply-journal.json').exists())
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)

    def test_malformed_transition_is_rejected_before_touching_install(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-config-') as folder:
            root=Path(folder); manager,config=self.install_fixture(root)
            value=json.loads(config.read_text());value['previousExecutableName']='../outside.exe';updater._write_json(config,value)
            with self.assertRaises(RuntimeError):updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
            self.assertEqual((root/'CivitaiDiscoveryBeta.exe').read_bytes(),b'old-beta')

    def test_pending_journal_blocks_new_download_cleanup_and_path_injection(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-journal-') as folder:
            root=Path(folder);manager,config=self.install_fixture(root)
            value=json.loads(config.read_text())
            backup=Path(value['backupRoot']);backup.mkdir(parents=True)
            (backup/'preserve.txt').write_text('original backup')
            binding={key:value.get(key) for key in ('installRoot','stagedRoot','backupRoot','resultPath','executableName','previousExecutableName','version')}
            updater._write_json(manager.root/'apply-journal.json',{'format':1,'binding':binding,
                'entries':[{'name':'../profile.json','hadOriginal':False,'phase':'intent'}]})
            with self.assertRaises(RuntimeError):manager.start_download()
            updater._write_json(manager.result_path,{'state':'installed','version':manager.current_version})
            manager.schedule_success_cleanup(delay=0)
            with self.assertRaises(RuntimeError):updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
            self.assertEqual((backup/'preserve.txt').read_text(),'original backup')
            self.assertEqual((root/'data/profile.json').read_text(),'private portable fixture')
            self.assertEqual((root/'CivitaiDiscoveryBeta.exe').read_bytes(),b'old-beta')

    def test_missing_original_backup_stays_failed_across_recovery_retries(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-lost-backup-') as folder:
            root=Path(folder);manager,config=self.install_fixture(root)
            value=json.loads(config.read_text())
            binding={key:value.get(key) for key in ('installRoot','stagedRoot','backupRoot','resultPath','executableName','previousExecutableName','version')}
            path=manager.root/'apply-journal.json'
            updater._write_json(path,{'format':1,'binding':binding,
                'entries':[{'name':'CivitaiDiscoveryBeta.exe','hadOriginal':True,'phase':'installed'}]})
            for _ in range(2):
                with self.assertRaises(RuntimeError):updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
                self.assertTrue(path.exists())
                self.assertEqual(json.loads(path.read_text())['entries'][0]['phase'],'installed')
                self.assertEqual((root/'CivitaiDiscoveryBeta.exe').read_bytes(),b'old-beta')

    @unittest.skipUnless(os.name=='nt','Windows sharing-lock semantics required')
    def test_real_windows_locked_beta_executable_rolls_back_safely(self):
        with tempfile.TemporaryDirectory(prefix='cad3-graduation-locked-') as folder:
            root=Path(folder);manager,config=self.install_fixture(root)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
            kernel.CreateFileW.restype=ctypes.c_void_p
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            handle=kernel.CreateFileW(str(root/'CivitaiDiscoveryBeta.exe'),0x80000000,0,None,3,0,None)
            self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
            replace=updater._replace_with_retry
            try:
                with patch.object(updater,'_replace_with_retry',lambda source,target:replace(source,target,timeout=.2)):
                    with self.assertRaises(PermissionError):updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
                self.assertTrue((root/'CivitaiDiscoveryBeta.exe').exists())
                self.assertFalse((root/'CivitaiArtistDiscovery.exe').exists())
            finally:kernel.CloseHandle(handle)
            self.assertEqual((root/'CivitaiDiscoveryBeta.exe').read_bytes(),b'old-beta')
            updater.apply_staged_update(config,launch=False,wait_for_process=lambda pid:None)
            self.assertTrue((root/'CivitaiArtistDiscovery.exe').exists())


if __name__=='__main__':unittest.main()

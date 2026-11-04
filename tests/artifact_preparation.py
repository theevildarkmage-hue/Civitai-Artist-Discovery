"""Shared beta/stable artifact identity, provenance and packaging rejection tests."""
import test_safety
import hashlib,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(test_safety.ROOT))
from discovery.build_config import DEFAULT
ROOT=test_safety.ROOT

class Artifacts(unittest.TestCase):
    def prepare(self,channel,damage=None):
        with tempfile.TemporaryDirectory(prefix='cad3-artifact-test-') as name:
            root=Path(name);application='CivitaiDiscoveryBeta' if channel=='beta' else 'CivitaiArtistDiscovery'
            package=root/application;package.mkdir();embedded=package/'_internal/discovery';embedded.mkdir(parents=True)
            config={**DEFAULT,'channel':channel,'version':'3.0.0-beta.14' if channel=='beta' else '3.0.0','preview':True}
            for path in [package/'build-configuration.json',embedded/'build-configuration.json']:path.write_text(json.dumps(config))
            exe=package/(application+'.exe');exe.write_bytes(b'synthetic artifact test executable; never launched')
            commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
            (package/'build-provenance.json').write_text(json.dumps({'sourceCommit':commit,'sourceDirty':True,'executableSha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'buildConfiguration':config}))
            if damage=='credential':(package/'oauth_tokens.dpapi').write_bytes(b'not-a-real-credential')
            if damage in ('credential-uppercase','sqlite-sidecar','environment-local','profile-directory'):
                relative={'credential-uppercase':'OAuth_Tokens.DPAPI','sqlite-sidecar':'taste.sqlite3-wal',
                    'environment-local':'.env.local','profile-directory':'Data/credentials.json'}[damage]
                rejected=package/relative;rejected.parent.mkdir(parents=True,exist_ok=True);rejected.write_text('synthetic rejection fixture')
            if damage=='exe':exe.write_bytes(b'changed bytes')
            if damage=='configuration':(embedded/'build-configuration.json').write_text('{}')
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/prepare-artifacts.py'),'--package',str(package),'--output',str(root/'artifacts'),'--preview'],cwd=ROOT,capture_output=True,text=True)
            if damage:self.assertNotEqual(result.returncode,0);return
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((root/'artifacts/release-manifest.json').read_text())
            self.assertEqual(manifest['applicationId'],application);self.assertEqual(manifest['channel'],channel)
            self.assertTrue(manifest['preview']);self.assertFalse(manifest['published'])
            archive=root/'artifacts'/(application+'-'+config['version']+'.zip')
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),manifest['sha256'][archive.name])
            with zipfile.ZipFile(archive) as z:
                self.assertIn(application+'/'+application+'.exe',z.namelist())
                self.assertEqual(json.loads(z.read(application+'/release-identity.json'))['sourceCommit'],commit)
    def test_both_layouts(self):
        for channel in ('beta','stable'):
            with self.subTest(channel=channel):self.prepare(channel)
    def test_rejects_profile_or_tampered_inputs(self):
        for channel in ('beta','stable'):
            for damage in ('credential','credential-uppercase','sqlite-sidecar','environment-local','profile-directory','exe','configuration'):
                with self.subTest(channel=channel,damage=damage):self.prepare(channel,damage)
if __name__=='__main__':unittest.main()

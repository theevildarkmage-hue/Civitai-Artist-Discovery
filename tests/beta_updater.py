"""Strict beta discovery, immutable tag identity and safe source revisions."""
import test_safety
import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery import updater

def release(version='3.0.0-beta.2'):
    name='CivitaiDiscoveryBeta-'+version+'.zip'
    return {'tag_name':'v'+version,'prerelease':True,'target_commitish':'a'*40,'assets':[{'name':name,'state':'uploaded','size':1,'digest':'sha256:'+'b'*64,'browser_download_url':'https://github.com/'+updater.REPOSITORY+'/releases/download/v'+version+'/'+name}]}

class BetaUpdaterTests(unittest.TestCase):
    def test_channel_rejections(self):
        self.assertIsNotNone(updater.select_release([release()],'3.0.0-beta.1'))
        for change in ({'tag_name':'v3.0.0'},{'prerelease':False},{'target_commitish':'main'},{'tag_name':'v4.0.0-beta.2'},{'assets':[]}):
            v=release();v.update(change);self.assertIsNone(updater.select_release([v],'3.0.0-beta.1'))
        self.assertIsNone(updater.select_release([release()],'2.2.1'))
    def test_tag_reference(self):
        v=updater.select_release([release()],'3.0.0-beta.1')
        with patch.object(updater,'_open_json',return_value={'object':{'type':'commit','sha':'a'*40}}):updater.verify_beta_ref(v)
        with patch.object(updater,'_open_json',return_value={'object':{'type':'commit','sha':'c'*40}}):
            with self.assertRaises(RuntimeError):updater.verify_beta_ref(v)
    def test_downloaded_wrong_application_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'fixture.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('CivitaiDiscoveryBeta/CivitaiDiscoveryBeta.exe','synthetic executable')
                z.writestr('CivitaiDiscoveryBeta/release-identity.json',json.dumps({'applicationId':'wrong'}))
            v=updater.select_release([release()],'3.0.0-beta.1');v['sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
            manager=updater.UpdateManager(root/'data',root/'install','3.0.0-beta.1',executable=root/'install/CivitaiDiscoveryBeta.exe',frozen=True)
            def download(url,target,size,progress):target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read_bytes());return v['sha256']
            with patch.object(updater,'verify_beta_ref'),patch.object(updater,'_download',side_effect=download):manager._download_worker(v)
            self.assertEqual(manager.job['phase'],'error');self.assertIn('identity',manager.job['error'])
    def test_source_update_and_dirty_rejection(self):
        spec=importlib.util.spec_from_file_location('source_update',test_safety.ROOT/'scripts/update-beta-source.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,text=True).strip()
            git('init');git('config','user.email','synthetic@example.invalid');git('config','user.name','Synthetic')
            (root/'.gitignore').write_text('data/\n');(root/'app.py').write_text('old');git('add','.');git('commit','-m','old');old=git('rev-parse','HEAD')
            (root/'app.py').write_text('new');git('commit','-am','new');target=git('rev-parse','HEAD');git('tag','v3.0.0-beta.2');git('checkout','--detach',old)
            (root/'data').mkdir();(root/'data/personal').write_text('preserve')
            result=module.update(root,'v3.0.0-beta.2',target,fetch=False);self.assertEqual(result['previousCommit'],old);self.assertEqual((root/'data/personal').read_text(),'preserve')
            (root/'app.py').write_text('local change')
            with self.assertRaises(ValueError):module.update(root,'v3.0.0-beta.2',target,fetch=False)

if __name__=='__main__':unittest.main()

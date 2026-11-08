"""Synthetic personal-identity variants, contextual matches and log boundary tests."""
import test_safety
import base64,hashlib,hmac,importlib.util,io,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery import privacy_patterns as P
from discovery.diagnostics import redact_sensitive,append_log
SYNTHETIC_SURNAME='Fixturefamily'
SYNTHETIC_EMAIL='person_fixture@example.invalid'
SYNTHETIC_ACCOUNT='fixturelocal'
def fingerprints(value):
    n=P.normalized(value)
    return {len(n):frozenset({hmac.new(b'synthetic-policy-key-for-tests',n.encode(),hashlib.sha256).hexdigest()})}
class PersonalIdentityPrivacy(unittest.TestCase):
    def setUp(self):
        self.patches=[patch.object(P,'_KEY',b'synthetic-policy-key-for-tests'),patch.object(P,'NAME_HASHES',fingerprints(SYNTHETIC_SURNAME)),patch.object(P,'EMAIL_HASHES',fingerprints(SYNTHETIC_EMAIL)),patch.object(P,'HOME_ACCOUNT_HASHES',fingerprints(SYNTHETIC_ACCOUNT))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def test_name_variations_separators_case_and_unicode(self):
        for value in ['Alex FixtureFamily','Fixturefamily, Alex','A. Fixturefamily','Alex_Fixturefamily','Alex-Fixturefamily','AlexFixturefamily','f_i_x_t_u_r_e_f_a_m_i_l_y','Ｆｉｘｔｕｒｅｆａｍｉｌｙ']:
            with self.subTest(shape=len(value)):self.assertIn('protected-name',P.categories(value))
    def test_common_transforms_and_utf16(self):
        raw='Alex '+SYNTHETIC_SURNAME
        variants=[''.join('%%%02x'%b for b in raw.encode()),''.join('&#%d;'%ord(c) for c in raw),''.join('\\u%04x'%ord(c) for c in raw),''.join('\\x%02x'%ord(c) for c in raw),base64.b64encode(raw.encode()).decode(),raw.encode().hex(),raw.encode('utf-16-le'),raw.encode('utf-16-be')]
        for value in variants:self.assertIn('protected-name',P.categories(value))
    def test_email_encoded_and_embedded(self):
        for value in [SYNTHETIC_EMAIL.upper(),'before-'+SYNTHETIC_EMAIL+'.txt',SYNTHETIC_EMAIL.replace('@','%40'),base64.b64encode(SYNTHETIC_EMAIL.encode()).decode()]:
            self.assertIn('protected-email',P.categories(value))
    def test_composite_unicode_in_utf16_and_binary_tree_names(self):
        value='Ｆｉｘｔｕｒｅｆａｍｉｌｙ'
        self.assertIn('protected-name',P.categories(value.encode('utf-16-le')))
        tree=b'100644 '+value.encode('utf-8')+b'.txt\x00'+b'\xff'*20
        self.assertIn('protected-name',P.categories(tree))
    def test_owner_home_windows_unix_wsl_and_query(self):
        for value in ['C:\\Users\\'+SYNTHETIC_ACCOUNT+'\\project','/home/'+SYNTHETIC_ACCOUNT+'/project','/Users/'+SYNTHETIC_ACCOUNT+'/project','/mnt/c/Users/'+SYNTHETIC_ACCOUNT+'/project','https://example.invalid/error?path=C%3A%5CUsers%5C'+SYNTHETIC_ACCOUNT+'%5Cproject']:
            self.assertIn('protected-home',P.categories(value))
    def test_public_urls_contacts_and_ordinary_names_stay_allowed(self):
        for value in ['Mark the item complete','Mark Twain','Example Contributor <contributor@example.invalid>','theevildarkmage-hue','260097500+theevildarkmage-hue@users.noreply.github.com','theevildarkmage@gmail.com','https://api.github.com/users/theevildarkmage-hue','https://civitai.red/user/ExampleArtist']:
            self.assertFalse(P.categories(value))
            if '@' not in value:self.assertEqual(P.redact_personal(value),value)
    def test_diagnostics_preserve_credentials_redaction_and_hide_home(self):
        self.assertNotIn('credential-canary',redact_sensitive('Bearer credential-canary'))
        self.assertNotIn(SYNTHETIC_ACCOUNT,redact_sensitive('C:\\Users\\'+SYNTHETIC_ACCOUNT+'\\trace.py'))
        self.assertNotIn('AnotherUser',redact_sensitive('/home/AnotherUser/project'))
    def test_actual_append_log_boundary_is_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            append_log(Path(folder),'failure: Alex '+SYNTHETIC_SURNAME+' '+SYNTHETIC_EMAIL)
            text=(Path(folder)/'error.log').read_text()
            self.assertNotIn(SYNTHETIC_SURNAME,text);self.assertNotIn(SYNTHETIC_EMAIL,text)
    def test_family_collision_requires_review_without_rewriting_attribution(self):
        value='Copyright Other '+SYNTHETIC_SURNAME
        self.assertTrue(P.categories(value));self.assertEqual(value,'Copyright Other '+SYNTHETIC_SURNAME)
    def test_real_child_process_output_redacts_home_and_preserves_failure_status(self):
        command=[sys.executable,str(ROOT/'scripts/run-redacted.py'),'--','-c',"import sys;print('/home/SyntheticBuildUser/project');sys.exit(7)"]
        result=subprocess.run(command,capture_output=True)
        self.assertEqual(result.returncode,7);self.assertNotIn(b'SyntheticBuildUser',result.stdout+result.stderr)
    def test_private_key_and_mapping_material_are_rejected(self):
        for value in [P._KEY,P._KEY.hex(),base64.b64encode(P._KEY).decode(),next(iter(P.NAME_HASHES.values())).__iter__().__next__()]:
            self.assertIn('private-policy-material',P.categories(value))
    def test_decoded_frozen_bytes_constants_keep_raw_key_and_utf16_coverage(self):
        spec=importlib.util.spec_from_file_location('package_identity_gate',ROOT/'scripts/check-personal-identity.py')
        module=importlib.util.module_from_spec(spec);sys.path.insert(0,str(ROOT/'scripts'));spec.loader.exec_module(module)
        for value in [P._KEY,('Alex '+SYNTHETIC_SURNAME).encode('utf-16-le')]:
            code=compile('DATA='+repr(value),'<synthetic-frozen-module>','exec')
            self.assertTrue(module.code_has_identity(code))
    def test_nested_archive_and_compressed_image_metadata_are_inspected(self):
        spec=importlib.util.spec_from_file_location('container_identity_gate',ROOT/'scripts/check-personal-identity.py')
        module=importlib.util.module_from_spec(spec);sys.path.insert(0,str(ROOT/'scripts'));spec.loader.exec_module(module)
        nested=io.BytesIO()
        with zipfile.ZipFile(nested,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('metadata.txt','Alex '+SYNTHETIC_SURNAME)
        outer=io.BytesIO()
        with zipfile.ZipFile(outer,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('base_library.zip',nested.getvalue())
        self.assertIn('protected-name',module.inspect_container(outer.getvalue(),'library.zip'))
        from PIL import Image,PngImagePlugin
        info=PngImagePlugin.PngInfo();info.add_itxt('Author','Alex '+SYNTHETIC_SURNAME,zip=True)
        image=io.BytesIO();Image.new('RGB',(2,2)).save(image,format='PNG',pnginfo=info)
        self.assertIn('protected-name',module.inspect_container(image.getvalue(),'cover.png'))
        with self.assertRaises(ValueError):module.inspect_container(b'not-a-reviewed-container','backup.pack')
        with self.assertRaises(ValueError):module.inspect_container(b'PK\x03\x04truncated','base_library.zip')

if __name__=='__main__':unittest.main()

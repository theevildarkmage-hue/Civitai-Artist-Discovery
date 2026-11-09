"""Safe manual recovery: actual SQLite/WAL, exclusion and rejection boundaries."""
import test_safety
import importlib.util,json,sqlite3,subprocess,tempfile,unittest
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('manual_copy',ROOT/'scripts/copy-local-data.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-manual-recovery-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir();self.target=self.root/'new'
    def test_actual_wal_and_settings_preserved_without_known_credentials(self):
        folder=self.source/'history';folder.mkdir()
        db=sqlite3.connect(folder/'history.sqlite3')
        try:
            db.execute('pragma journal_mode=WAL');db.execute('pragma wal_autocheckpoint=0')
            db.execute('create table days(day text,pages integer)');db.execute('insert into days values(?,?)',('2026-11-01',7));db.commit()
            self.assertGreater(Path(str(folder/'history.sqlite3')+'-wal').stat().st_size,0)
            (self.source/'settings.json').write_text(json.dumps({'autoCapture':False,'browsingLevels':[1,2]}))
            (self.source/'oauth_tokens.dpapi').write_bytes(b'synthetic-exclusion-canary')
            (self.source/'community').mkdir();(self.source/'community/session.json').write_text('{}')
            receipt=helper.copy_local_data(self.source,self.target)
            self.assertTrue(receipt['freshSignInRequired']);self.assertFalse(receipt['credentialsCopied'])
            self.assertEqual((self.source/'settings.json').read_bytes(),(self.target/'settings.json').read_bytes())
            with closing(sqlite3.connect(self.target/'history/history.sqlite3')) as copied:
                self.assertEqual(copied.execute('pragma quick_check').fetchone()[0],'ok')
                self.assertEqual(copied.execute('select * from days').fetchall(),[('2026-11-01',7)])
            self.assertFalse((self.target/'oauth_tokens.dpapi').exists());self.assertFalse((self.target/'community').exists())
            self.assertEqual(db.execute('select * from days').fetchall(),[('2026-11-01',7)])
        finally:db.close()
    def test_populated_destination_and_overlap_are_rejected(self):
        self.target.mkdir();(self.target/'keep.txt').write_text('preserve')
        for target in [self.target,self.source,self.source/'nested',self.root]:
            with self.assertRaises(ValueError):helper.copy_local_data(self.source,target)
        self.assertEqual((self.target/'keep.txt').read_text(),'preserve')
    def test_linked_input_escape_is_rejected(self):
        outside=self.root/'outside.json';outside.write_text('{}')
        try:(self.source/'settings.json').symlink_to(outside)
        except OSError:
            linked=self.source/'history';external=self.root/'outside-data';external.mkdir()
            (external/'history.sqlite3').write_bytes(b'synthetic outside input')
            quote=lambda p:"'"+str(p).replace("'","''")+"'"
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                'New-Item -ItemType Junction -Path '+quote(linked)+' -Target '+quote(external)+' | Out-Null'],capture_output=True)
            self.assertEqual(result.returncode,0,'Owned junction fixture could not be created')
        with self.assertRaises(ValueError):helper.copy_local_data(self.source,self.target)
        self.assertFalse(self.target.exists());self.assertEqual(outside.read_text(),'{}')
    def test_corrupt_database_preserves_source_and_reports_failure(self):
        folder=self.source/'history';folder.mkdir();file=folder/'history.sqlite3';file.write_bytes(b'synthetic corrupt database')
        before=file.read_bytes()
        with self.assertRaises((ValueError,sqlite3.DatabaseError)):helper.copy_local_data(self.source,self.target)
        self.assertEqual(file.read_bytes(),before)
    def test_invalid_settings_are_not_silently_imported(self):
        file=self.source/'settings.json';file.write_text('{invalid')
        with self.assertRaises(ValueError):helper.copy_local_data(self.source,self.target)
        self.assertEqual(file.read_text(),'{invalid')
    def test_empty_destination_and_missing_optional_files_are_supported(self):
        self.target.mkdir();result=helper.copy_local_data(self.source,self.target)
        self.assertEqual(result['copied'],[]);self.assertTrue(result['sourcePreserved'])

if __name__=='__main__':unittest.main()

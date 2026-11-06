"""Fixed-query audit preflight and transport restrictions; no real credentials."""
import test_safety,importlib.util,tempfile,json,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('audit',test_safety.ROOT/'scripts/community-audit-readonly.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
class Audit(unittest.TestCase):
    def read(self,path='',query=None):
        self.calls.append((path,query))
        if path=='':return {'id':self.project,'region':'us-west-2','status':'ACTIVE_HEALTHY'}
        if path=='/functions':return [{'slug':'community-v1','version':28,'status':'ACTIVE','credential':'synthetic-never-return'}]
        if query==audit.AUTHORITY:return [{'current_user':'supabase_read_only_user','current_database':'postgres','read_only':self.mode}]
        return [{'definition':'Authorization: Bearer synthetic-sensitive-value'}]
    def setUp(self):self.calls=[];self.project=audit.REF;self.mode='on'
    def test_fixed_queries_and_minimized_output(self):
        result=audit.collect(self.read)
        self.assertEqual(len(result['querySha256']),6)
        self.assertNotIn('synthetic-sensitive-value',json.dumps(result))
        self.assertNotIn('synthetic-never-return',json.dumps(result))
        self.assertTrue(all(path in ('','/functions','/database/query/read-only') for path,query in self.calls))
    def test_wrong_project_stops_before_sql(self):
        self.project='wrong'
        with self.assertRaises(RuntimeError):audit.collect(self.read)
        self.assertEqual(len(self.calls),1)
    def test_writable_authority_rejected(self):
        self.mode='off'
        with self.assertRaises(RuntimeError):audit.collect(self.read)
        self.assertEqual(len(self.calls),2)
    def test_no_arbitrary_endpoint_or_write_transport(self):
        read=audit.transport('synthetic')
        for path in ('/secrets','/database/query','/functions/community-v1','/v1/profile'):
            with self.assertRaises(ValueError):read(path)
        with self.assertRaises(ValueError):read('/database/query/read-only')
if __name__=='__main__':unittest.main()

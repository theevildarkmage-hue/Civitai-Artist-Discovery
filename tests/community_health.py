"""Read-only operational health must reject accidental registry activation."""
import test_safety
import hashlib,importlib.util,io,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('health',test_safety.ROOT/'scripts/community-health-readonly.py')
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)
class HealthTests(unittest.TestCase):
    def run_health(self,registry=None,expiry='2000-01-01T00:00:00Z',migrations=8,version=24,jwt=False):
        def response(request,**kwargs):
            path=request.full_url
            if path.endswith('/functions'):data=[dict(slug='community-v1',version=version,status='ACTIVE',verify_jwt=jwt)]
            elif path.endswith('/secrets'):
                values={'COMMUNITY_BETA_STATE':'disabled','COMMUNITY_ALLOWED_RATINGS':'1,2','COMMUNITY_OWNER_TEST_EXPIRES_AT':expiry}
                if registry is not None:values['DISCOVERY_REGISTRY_STATE']=registry
                data=[{'name':k,'value':hashlib.sha256(v.encode()).hexdigest()} for k,v in values.items()]
            elif path.endswith('/database/query/read-only'):
                query=json.loads(request.data)['query'];self.assertTrue(query.lstrip().lower().startswith('select'))
                data=[dict(recommendations=3,audits=3,replay_records=3,registry_records=1,migrations=migrations,member_overcaps=0,artist_overcaps=0,audit_mismatches=0,global_requests_last_bucket=0)]
            else:data=dict(id=health.REF,region='us-west-2',status='ACTIVE_HEALTHY')
            return io.BytesIO(json.dumps(data).encode())
        with tempfile.TemporaryDirectory(prefix='cad3-health-test-') as directory:
            target=Path(directory)/'health.json'
            with patch.dict(os.environ,{'SUPABASE_ACCESS_TOKEN':'synthetic-not-a-token'}),patch.object(sys,'argv',['health','--output',str(target),'--expected-function-version','24']),patch.object(health.urllib.request,'urlopen',side_effect=response),patch('sys.stdout',io.StringIO()):code=health.main()
            result=json.loads(target.read_text());self.assertFalse(result['hostedMutation']);return code,result
    def test_absent_and_disabled_registry_are_safe(self):
        for value in (None,'disabled'):
            with self.subTest(value=value):self.assertEqual(self.run_health(value)[0],0)
    def test_enabled_owner_test_and_unknown_fail(self):
        for value in ('enabled','owner_test','unknown'):
            with self.subTest(value=value):self.assertEqual(self.run_health(value)[0],1)
    def test_nonclosed_window_fails(self):self.assertEqual(self.run_health(expiry='2099-01-01T00:00:00Z')[0],1)
    def test_old_schema_fails(self):self.assertEqual(self.run_health(migrations=7)[0],1)
    def test_wrong_revision_fails(self):self.assertEqual(self.run_health(version=25)[0],1)
    def test_gateway_mismatch_fails(self):self.assertEqual(self.run_health(jwt=True)[0],1)
if __name__=='__main__':unittest.main()

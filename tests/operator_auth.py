"""Operator CLI secret handling with synthetic Auth transport; not hosted MFA proof."""
import test_safety
import contextlib,importlib.util,io,json,os,sys,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('operator_cli',test_safety.ROOT/'scripts/community-auth-operator.py');cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)

class OperatorAuthTests(unittest.TestCase):
    def flow(self,enroll):
        calls=[];factor='00000000-0000-0000-0000-000000000003';owner='00000000-0000-0000-0000-000000000001'
        class Response:
            def __init__(self,value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps(self.value).encode()
        class Transport:
            def open(self,request,**kwargs):
                calls.append((request.full_url,request.get_method(),request.data))
                path=request.full_url.split('.supabase.co')[1]
                if path.startswith('/auth/v1/token'):return Response({'access_token':'synthetic-aal1','refresh_token':'synthetic-refresh'})
                if path=='/auth/v1/user':return Response({'id':owner,'factors':[] if enroll else [{'id':factor,'factor_type':'totp','status':'verified'}]})
                if path=='/auth/v1/factors':return Response({'id':factor,'totp':{'qr_code':'<svg xmlns="http://www.w3.org/2000/svg"></svg>'}})
                if path.endswith('/challenge'):return Response({'id':'synthetic-challenge'})
                if path.endswith('/verify'):return Response({'access_token':'synthetic-aal2'})
                if path=='/rest/v1/rpc/community_admin':return Response({'users':1})
                if path.startswith('/auth/v1/logout'):return Response({})
                raise AssertionError('Unexpected transport path')
        output=io.StringIO()
        with patch.object(sys,'argv',['operator','enroll' if enroll else 'registry_stats']),patch.object(sys.stdin,'isatty',return_value=True),patch.dict(os.environ,{'SUPABASE_PUBLISHABLE_KEY':'sb_publishable_synthetic'}),patch('builtins.input',return_value='synthetic@example.invalid'),patch.object(cli.getpass,'getpass',side_effect=['synthetic-private-password','123456']),patch.object(cli.urllib.request,'build_opener',return_value=Transport()),patch.object(cli.webbrowser,'open') as browser,contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(),0)
        text=output.getvalue()
        for secret in ('synthetic-private-password','123456','synthetic-aal1','synthetic-aal2','synthetic-refresh','<svg'):
            self.assertNotIn(secret,text)
        self.assertTrue(calls[-1][0].endswith('/auth/v1/logout?scope=local'))
        self.assertEqual(any('/rpc/community_admin' in c[0] for c in calls),not enroll)
        if enroll:self.assertTrue(browser.call_args.args[0].startswith('http://127.0.0.1:'))
    def test_operator_login_and_signout(self):self.flow(False)
    def test_enrollment_does_not_create_operator_authority(self):self.flow(True)
    def test_noninteractive_secret_input_refused(self):
        with patch.object(sys,'argv',['operator','registry_stats']),patch.object(sys.stdin,'isatty',return_value=False),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):cli.main()

if __name__=='__main__':unittest.main()

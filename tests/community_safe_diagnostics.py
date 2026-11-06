"""No exception/request data enters diagnostics; exercise actual response handlers."""
import test_safety
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import diagnostics
import server

class SafeDiagnostics(unittest.TestCase):
    def test_exception_canaries_never_logged_or_returned(self):
        secrets=['synthetic-access-canary','synthetic-refresh-canary','sdc_'+'x'*43,'sdr_'+'y'*43,
                 'synthetic-signed-assertion','synthetic-device-private']
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);diagnostics._COMMUNITY_RECENT.clear()
            for error in (RuntimeError(' '.join(secrets)),ValueError('https://invalid/?code='+secrets[0]),TimeoutError(secrets[1]),OSError(secrets[2]),type('Class_'+secrets[0],(Exception,),{})(secrets[-1])):
                error.__cause__=RuntimeError(secrets[3]);error.headers={'Authorization':'Bearer '+secrets[0]};error.url=secrets[4]
                handler=object.__new__(server.Handler);handler.client_address=('127.0.0.1',10000)
                handler.require_community_session=lambda:None
                responses=[];handler.json_response=lambda value,status=200:responses.append((value,status))
                client=SimpleNamespace(mode='supabase')
                def fail(*args,**kwargs):raise error
                client.request=fail
                with patch.object(server,'DATA_ROOT',root),patch.object(server,'community_client',return_value=client),patch('discovery.community_beta.require_enabled'),patch('discovery.community_security.test_identity',return_value=None),patch('discovery.community_security.fixture_enabled',return_value=False):
                    handler.community_response('gallery',{})
                self.assertEqual(responses[0][1],503)
                self.assertRegex(responses[0][0]['diagnosticId'],r'^[0-9a-f]{16}$')
                for secret in secrets:self.assertNotIn(secret,json.dumps(responses))
            text=(root/'error.log').read_text()
            for secret in secrets:self.assertNotIn(secret,text)
            for category in ('unexpected','invalid_response','timeout','transport'):self.assertIn('category='+category,text)
            self.assertNotIn('Traceback',text)
    def test_repeated_outages_are_coalesced_and_cardinality_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);diagnostics._COMMUNITY_RECENT.clear()
            ids={diagnostics.community_failure(root,'gallery',RuntimeError('synthetic-secret')) for _ in range(1000)}
            self.assertEqual(len(ids),1);self.assertEqual(len((root/'error.log').read_text().splitlines()),1)
            with patch.object(diagnostics,'LOG_BYTES',250):
                for i in range(100):
                    with patch.object(diagnostics.time,'monotonic',return_value=i*61+100000):diagnostics.community_failure(root,'detail',OSError('synthetic-secret'))
            self.assertLessEqual((root/'error.log').stat().st_size,400)
            for i in range(80):diagnostics.community_failure(root/str(i),'unknown-secret-operation',ValueError('synthetic-secret'))
            self.assertLessEqual(len(diagnostics._COMMUNITY_RECENT),64)

if __name__=='__main__':unittest.main()

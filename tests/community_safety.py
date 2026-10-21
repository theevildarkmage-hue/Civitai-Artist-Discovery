"""No unsafe test target can reach the transport or acquire a fixture identity."""
import test_safety
import http.client
import os
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery.community import CommunityClient, CivitaiVerifier, CommunityError, FixtureVerifier, ContentProfile, build_community


class SafetyTests(unittest.TestCase):
    def test_unsafe_targets_rejected_before_network(self):
        for url in ('http://127.0.0.1:8906','http://127.0.0.1:33101','https://example.com','https://x.supabase.co'):
            with patch('socket.create_connection',side_effect=AssertionError('Network attempted')):
                with self.assertRaises(PermissionError):test_safety.address(url)

    def test_unknown_loopback_is_not_a_test_endpoint(self):
        with patch('socket.create_connection',side_effect=AssertionError('Network attempted')):
            with self.assertRaises(PermissionError):
                http.client.HTTPConnection('127.0.0.1',1).request('POST','/api/settings',body='{}')

    def test_forbidden_child_port_rejected_before_spawn(self):
        env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(test_safety.ROOT/'data-test-safety')}
        with self.assertRaises(PermissionError):
            subprocess.Popen([sys.executable,'server.py','--port','8906'],env=env)

    def test_personal_data_root_rejected(self):
        for path in (test_safety.ROOT.parent/'Civitai Desktop Client/data',test_safety.ROOT.parent,test_safety.ROOT,
                     test_safety.ROOT/'data-dev-3.0',test_safety.ROOT/'data-test-community'):
            with self.assertRaises(PermissionError):test_safety.isolated_root(path)

    def test_occupied_port_rejected_before_spawn(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            port=listener.getsockname()[1]
            env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(test_safety.ROOT/'data-test-safety')}
            with self.assertRaises(PermissionError):
                subprocess.Popen([sys.executable,'server.py','--port',str(port)],env=env)

    def test_server_refuses_test_auth_on_interactive_profile(self):
        from discovery.community_dev import test_identity
        with patch.dict(os.environ,{'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_MODE':'fixture',
                'CAD_COMMUNITY_TESTING':'1','CAD_TEST_INSTANCE':'x'*48}):
            with self.assertRaises(RuntimeError):test_identity(test_safety.ROOT/'data-test-community')

    def test_fake_identity_never_overrides_real_verifier(self):
        client=CommunityClient(None,CivitaiVerifier(),lambda:'synthetic-token',lambda _:ContentProfile())
        with self.assertRaises(CommunityError) as error:
            client.request('status',{},fixture_token='fixture:1')
        self.assertEqual('FORBIDDEN',error.exception.code)

    def test_fixture_requires_explicit_development(self):
        with tempfile.TemporaryDirectory(prefix='cad3-safety-') as root:
            with patch.dict(os.environ,{'CAD_COMMUNITY_MODE':'fixture','CAD_DEVELOPMENT':'0'}):
                with self.assertRaises(RuntimeError):build_community(Path(root),lambda _:None,lambda:None)

if __name__=='__main__':unittest.main()

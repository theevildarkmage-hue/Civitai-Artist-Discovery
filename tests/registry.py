"""Automatic registry scheduling, bounded heartbeat and failure isolation."""
import test_safety
import sys,time,threading,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(test_safety.ROOT))
from discovery.registry import RegistryClient

class RegistryTests(unittest.TestCase):
    def idle(self,client):
        for _ in range(200):
            if not client.active:return
            time.sleep(.005)
        self.fail('Registry worker did not finish')

    def test_automatic_registration_and_twelve_hour_heartbeat(self):
        calls=[];now=[100.0]
        def send(url,token,payload):calls.append((url,token,payload));return {'apiVersion':1,'registered':True}
        client=RegistryClient('3.0.0-beta.1',clock=lambda:now[0],send=send)
        self.assertTrue(client.authenticated({'id':7},lambda:'synthetic-token'));self.idle(client)
        self.assertEqual(len(calls),1)
        self.assertEqual(set(calls[0][2]),{'apiVersion','operation','appVersion','platform'})
        self.assertEqual(calls[0][2]['operation'],'register')
        self.assertFalse(client.authenticated({'id':7},lambda:self.fail('Duplicate token read')))
        now[0]+=43199;self.assertFalse(client.authenticated({'id':7},lambda:'synthetic-token'))
        now[0]+=1;self.assertTrue(client.authenticated({'id':7},lambda:'synthetic-token'));self.idle(client)
        self.assertEqual(len(calls),2)

    def test_no_anonymous_or_invalid_identity_registration(self):
        client=RegistryClient('3.0.0-beta.1',send=lambda *args:self.fail('Unexpected network'))
        for identity in ({},{'id':True},{'id':0},{'id':-1},{'id':'7'}):
            self.assertFalse(client.authenticated(identity,lambda:self.fail('Unexpected token read')))

    def test_outage_is_nonblocking_and_backs_off(self):
        calls=[];now=[100.0];release=threading.Event();entered=threading.Event()
        def fail(*args):calls.append(True);entered.set();release.wait(2);raise OSError('Synthetic outage')
        client=RegistryClient('3.0.0-beta.1',clock=lambda:now[0],send=fail)
        start=time.monotonic();self.assertTrue(client.authenticated({'id':7},lambda:'synthetic'));self.assertLess(time.monotonic()-start,.5)
        self.assertTrue(entered.wait(1));self.assertFalse(client.authenticated({'id':8},lambda:'synthetic'))
        release.set();self.idle(client)
        self.assertFalse(client.authenticated({'id':7},lambda:'synthetic'))
        now[0]+=7200;self.assertTrue(client.authenticated({'id':7},lambda:'synthetic'));self.idle(client)
        self.assertEqual(len(calls),2)
        self.assertFalse(hasattr(client,'path'))
        self.assertFalse(hasattr(client,'set_consent'))

    def test_hosted_disable_defers_without_disabling_client(self):
        now=[100.0];client=RegistryClient('3.0.0-beta.1',clock=lambda:now[0],send=lambda *args:{'apiVersion':1,'registered':False,'retryAfterSeconds':43200})
        self.assertTrue(client.authenticated({'id':7},lambda:'synthetic'));self.idle(client)
        now[0]+=7200;self.assertFalse(client.authenticated({'id':7},lambda:'synthetic'))
        now[0]+=36000;self.assertTrue(client.authenticated({'id':7},lambda:'synthetic'));self.idle(client)

    def test_worker_start_failure_never_breaks_authentication(self):
        client=RegistryClient('3.0.0-beta.1')
        with patch('discovery.registry.threading.Thread.start',side_effect=RuntimeError('No worker available')):
            self.assertFalse(client.authenticated({'id':7},lambda:self.fail('Token read')))
        self.assertFalse(client.active)

    def test_failure_diagnostic_never_logs_exception_or_credential(self):
        def fail(*args):raise OSError('secret-credential-and-request-body')
        client=RegistryClient('3.0.0-beta.1',send=fail)
        with self.assertLogs('discovery.registry',level='WARNING') as logs:
            client.authenticated({'id':7},lambda:'secret-token');self.idle(client)
        self.assertEqual(logs.output,['WARNING:discovery.registry:Registry heartbeat deferred: INTERNAL_OR_TRANSPORT'])

    def test_inflight_calls_are_deduplicated(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def send(*args):calls.append(True);entered.set();release.wait(2);return {'apiVersion':1,'registered':True}
        client=RegistryClient('3.0.0-beta.1',send=send)
        self.assertTrue(client.authenticated({'id':7},lambda:'synthetic'));self.assertTrue(entered.wait(1))
        for _ in range(20):self.assertFalse(client.authenticated({'id':7},lambda:'synthetic'))
        release.set();self.idle(client);self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()

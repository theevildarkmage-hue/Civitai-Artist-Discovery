"""Registry consent, bounded scheduling and failure isolation, synthetic transport."""
import test_safety
import sys,tempfile,time,threading,unittest
from pathlib import Path
sys.path.insert(0,str(test_safety.ROOT))
from discovery.registry import RegistryClient

class RegistryTests(unittest.TestCase):
    def test_default_collects_nothing_and_never_reads_credential(self):
        with tempfile.TemporaryDirectory() as root:
            client=RegistryClient(root,'3.0.0-beta.1')
            self.assertFalse(client.authenticated({'id':7},lambda:self.fail('Credential read')))
            self.assertFalse(client.path.exists())
    def test_consent_identity_and_heartbeat(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[];now=[100.0];done=threading.Event()
            def send(url,token,payload):calls.append((url,payload));done.set();return {'apiVersion':1,'registered':True}
            c=RegistryClient(root,'3.0.0-beta.1',enabled=True,clock=lambda:now[0],send=send)
            self.assertFalse(c.authenticated({'id':7},lambda:'synthetic'))
            c.set_consent(True)
            self.assertFalse(c.authenticated({'id':True},lambda:'synthetic'))
            self.assertTrue(c.authenticated({'id':7},lambda:'synthetic'));self.assertTrue(done.wait(2))
            for _ in range(100):
                if not c.active:break
                time.sleep(.01)
            self.assertFalse(c.authenticated({'id':7},lambda:'synthetic'))
            self.assertNotIn('memberId',calls[0][1]);self.assertNotIn('token',c.path.read_text())
            now[0]+=43201;done.clear()
            self.assertTrue(c.authenticated({'id':7},lambda:'synthetic'));self.assertTrue(done.wait(2))
            c.set_consent(False);self.assertFalse(c.authenticated({'id':8},lambda:'synthetic'))
    def test_outage_has_no_queue_or_local_data_damage(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'settings.json';p.write_text('{"preserved":true}')
            def fail(*args):raise OSError('Synthetic outage')
            c=RegistryClient(root,'3.0.0-beta.1',enabled=True,send=fail);c.set_consent(True)
            self.assertTrue(c.authenticated({'id':7},lambda:'synthetic'))
            for _ in range(100):
                if not c.active:break
                time.sleep(.01)
            self.assertFalse(c.authenticated({'id':7},lambda:'synthetic'))
            self.assertEqual(p.read_text(),'{"preserved":true}');self.assertEqual(set(x.name for x in Path(root).iterdir()),{'settings.json','registry-consent.json'})

if __name__=='__main__':unittest.main()

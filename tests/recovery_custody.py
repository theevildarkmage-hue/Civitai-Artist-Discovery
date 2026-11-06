"""Do not mislabel same-account or tampered canaries as independent recovery."""
import test_safety
import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('custody',test_safety.ROOT/'scripts/rehearse-recovery-custody.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
@unittest.skipUnless(sys.platform=='win32','DPAPI canary is Windows-only')
class CustodyTests(unittest.TestCase):
    def test_real_roundtrip_tamper_and_same_account_refusal(self):
        with tempfile.TemporaryDirectory(prefix='cad3-custody-test-') as t:
            path=Path(t)/'canary';r=m.prepare(path)
            self.assertTrue(r['sameAccountRoundTrip']);self.assertTrue(r['tamperDenied']);self.assertFalse(r['independentAccountExecuted']);self.assertFalse(r['portableRecoveryDemonstrated'])
            with self.assertRaisesRegex(RuntimeError,'Same account'):m.probe(path)
            with self.assertRaises(FileExistsError):m.prepare(path)
    def test_corrupt_transfer_rejected_before_decryption(self):
        with tempfile.TemporaryDirectory(prefix='cad3-custody-test-') as t:
            path=Path(t)/'canary';m.prepare(path);(path/'canary.dpapi').write_bytes(b'corrupt synthetic canary')
            with self.assertRaisesRegex(RuntimeError,'integrity mismatch'):m.probe(path)
    def test_independent_branch_is_explicitly_simulated(self):
        with tempfile.TemporaryDirectory(prefix='cad3-custody-test-') as t:
            path=Path(t)/'canary';m.prepare(path)
            with patch.object(m,'identity_hash',return_value='different-fixture-account'),patch.object(m,'transform',side_effect=RuntimeError('fixture denial')):
                r=m.probe(path);self.assertTrue(r['originalAccountProtectionDenied']);self.assertFalse(r['portableRecoveryDemonstrated'])
if __name__=='__main__':unittest.main()

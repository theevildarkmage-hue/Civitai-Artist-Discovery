"""Real account-bound encryption checks using synthetic data, no credential access."""
import test_safety
import sys
import unittest
sys.path.insert(0,str(test_safety.ROOT))
from discovery.secure_backup import transform


@unittest.skipUnless(sys.platform=='win32','Windows account-bound DPAPI required')
class BackupProtectionTests(unittest.TestCase):
    def test_round_trip_without_plaintext_file(self):
        payload=b'Synthetic PostgreSQL archive metadata only.'*2000
        protected=transform(payload)
        self.assertNotIn(payload[:40],protected)
        self.assertEqual(payload,transform(protected,decrypt=True))
    def test_tampering_denied(self):
        protected=bytearray(transform(b'Synthetic metadata'))
        protected[-1]^=1
        with self.assertRaises(RuntimeError):transform(bytes(protected),decrypt=True)
    def test_wrong_archive_denied(self):
        with self.assertRaises(RuntimeError):transform(b'not a DPAPI archive',decrypt=True)


if __name__=='__main__':unittest.main()

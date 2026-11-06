"""Transient Windows locks retry atomically; permanent failures remain bounded."""
import test_safety
import unittest
from pathlib import Path
from unittest.mock import patch
from discovery import updater

class ReplacementRetryTests(unittest.TestCase):
 def test_transient_lock_clears_without_copying_or_deleting(self):
  with patch.object(Path,'replace',side_effect=[PermissionError('synthetic lock'),None]) as replace,patch.object(updater.time,'sleep') as sleep,patch.object(updater.os,'name','nt'):
   updater._replace_with_retry(Path('old'),Path('backup'))
  self.assertEqual(replace.call_count,2);sleep.assert_called_once_with(.2)
 def test_permanent_lock_stops_at_deadline(self):
  with patch.object(Path,'replace',side_effect=PermissionError('synthetic lock')) as replace,patch.object(updater.time,'monotonic',side_effect=[0,0,9]),patch.object(updater.time,'sleep') as sleep,patch.object(updater.os,'name','nt'):
   with self.assertRaises(PermissionError):updater._replace_with_retry(Path('old'),Path('backup'))
  self.assertEqual(replace.call_count,2);sleep.assert_called_once()
 def test_other_io_errors_are_not_retried(self):
  with patch.object(Path,'replace',side_effect=OSError('synthetic disk failure')) as replace,patch.object(updater.time,'sleep') as sleep:
   with self.assertRaises(OSError):updater._replace_with_retry(Path('old'),Path('backup'))
  replace.assert_called_once();sleep.assert_not_called()
if __name__=='__main__':unittest.main()

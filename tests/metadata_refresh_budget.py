"""Bound actual worker fan-out across caches; verify metadata lookup is offline."""
import test_safety
import sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery.cache import MetadataCache
from discovery.history import HistoryArchive

class RefreshBudgetTests(unittest.TestCase):
    def test_background_admission_has_no_waiting_thread_queue(self):
        with tempfile.TemporaryDirectory() as root:
            caches=[MetadataCache(Path(root)/f'{i}.db') for i in range(2)]
            release=threading.Event();entered=threading.Event();lock=threading.Lock();active=[0,0]
            def loader():
                with lock:
                    active[0]+=1;active[1]=max(active);entered.set()
                release.wait(3)
                with lock:active[0]-=1
                return {'id':1}
            try:
                admitted=sum(cache.refresh(str(i),loader,background=True) for i in range(60) for cache in caches)
                self.assertTrue(entered.wait(1));self.assertEqual(admitted,3)
                self.assertLessEqual(active[1],3);self.assertEqual(sum(len(cache.pending) for cache in caches),3)
            finally:release.set()
            for _ in range(200):
                if not any(cache.pending for cache in caches):break
                time.sleep(.01)
            self.assertFalse(any(cache.pending for cache in caches))
            self.assertTrue(caches[0].refresh('after',lambda:{},background=False))
    def test_start_failure_releases_admission(self):
        with tempfile.TemporaryDirectory() as root:
            cache=MetadataCache(Path(root)/'cache.db')
            with patch('discovery.cache.threading.Thread.start',side_effect=RuntimeError('Synthetic failure')):
                for i in range(10):self.assertFalse(cache.refresh(str(i),lambda:{},background=True))
            self.assertEqual(cache.pending,set())
            self.assertTrue(cache.refresh('after',lambda:{},background=True))
            for _ in range(200):
                if not cache.pending:break
                time.sleep(.01)
    def test_unknown_model_lookup_never_fetches_detail(self):
        with tempfile.TemporaryDirectory() as root:
            archive=HistoryArchive(Path(root))
            with patch.object(archive,'_request',side_effect=AssertionError('Unexpected provider request')):
                self.assertEqual(archive.cached_base_model(987654321),'Unknown')

if __name__=='__main__':unittest.main()

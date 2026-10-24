"""Isolated real-loopback OAuth, preference rollback, cache eviction and logs."""
import test_safety
import tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from discovery.oauth import Callback
from discovery.settings import AppSettings
from discovery.diagnostics import append_log
import server

class Operations(unittest.TestCase):
    def test_backup_only_allows_live_throttle_drift(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('backup_tools',Path(__file__).resolve().parents[1]/'scripts/backup-community-hosted.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        original={'public.community_rate_buckets':[1],'public.community_recommendations':[42],'migrations':[9]}
        current={**original,'public.community_rate_buckets':[2]}
        self.assertEqual(module.source_interval_changes(original,current),['public.community_rate_buckets'])
        self.assertEqual(module.source_interval_changes(original,original),[])
        for key in ('public.community_recommendations','migrations','unexpected_table'):
            with self.assertRaises(RuntimeError):module.source_interval_changes(original,{**current,key:[]})

    def test_invalid_callback_does_not_cancel_login(self):
        listener=ThreadingHTTPServer(('127.0.0.1',0),Callback)
        listener.expected_state='synthetic-valid-state';listener.result=None
        listener.result_lock=threading.Lock()
        worker=threading.Thread(target=listener.serve_forever,daemon=True);worker.start()
        base='http://127.0.0.1:'+str(listener.server_port)+'/oauth/callback?'
        try:
            for query in ('code=bogus','state=wrong&code=bogus','state=%C3%A9&code=bogus'):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base+query,timeout=5)
                self.assertEqual(caught.exception.code,400);caught.exception.close()
                self.assertIsNone(listener.result)
            with urllib.request.urlopen(base+'state=synthetic-valid-state&code=synthetic',timeout=5) as response:
                self.assertEqual(response.status,200)
            self.assertEqual(listener.result['code'],'synthetic')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base+'state=synthetic-valid-state&code=replacement',timeout=5)
            self.assertEqual(caught.exception.code,409);caught.exception.close()
            self.assertEqual(listener.result['code'],'synthetic')
        finally:listener.shutdown();listener.server_close();worker.join()

    def test_complete_preference_rollback_and_first_run(self):
        with tempfile.TemporaryDirectory() as temp:
            settings=AppSettings(Path(temp)/'settings.json')
            settings.update(media_type_value='video',high_volume_threshold_value=150)
            before=settings.path.read_bytes()
            def fail(value):raise OSError('Synthetic filter failure')
            with self.assertRaises(OSError):
                settings.transactional_update(effect=fail,media_type_value='image',
                    high_volume_threshold_value=50,browsing_levels_value=[1])
            self.assertEqual(settings.path.read_bytes(),before)
            settings.path.unlink()
            with self.assertRaises(OSError):settings.transactional_update(effect=fail,media_type_value='video')
            self.assertFalse(settings.path.exists())
            self.assertEqual(settings.transactional_update(media_type_value='image')['mediaType'],'image')

    def test_cache_eviction_is_bounded_and_recent_view_survives(self):
        server.ORDER_CACHE.clear()
        try:
            with patch.object(server,'day_view_order',return_value=(['artist'],1)) as compute:
                def read(key):return server.cached_day_view_order(key,'foryou',None,{},None,'session',set())
                for i in range(server.ORDER_CACHE_LIMIT):read(str(i))
                read('0');read('extra')
                self.assertEqual(len(server.ORDER_CACHE),server.ORDER_CACHE_LIMIT)
                self.assertNotIn(('1','foryou',None,None),server.ORDER_CACHE)
                count=compute.call_count;read('0')
                self.assertEqual(compute.call_count,count)
                read('1');self.assertEqual(compute.call_count,count+1)
        finally:server.ORDER_CACHE.clear()

    def test_rotated_logs_redact_both_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch('discovery.diagnostics.LOG_BYTES',100):
                append_log(root,'Bearer synthetic-secret '+('x'*80))
                append_log(root,'access_token=another-synthetic-secret')
            self.assertTrue((root/'error.log.1').exists())
            text=''.join(p.read_text() for p in root.glob('error.log*'))
            self.assertNotIn('synthetic-secret',text)
            self.assertIn('[redacted]',text)

if __name__=='__main__':unittest.main()

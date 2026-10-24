"""Bounded external payloads, disposable thumbnails and busy recovery polling."""
import test_safety,gc,json,tempfile,threading,unittest,time
import io,urllib.error
from unittest.mock import patch,Mock
from pathlib import Path
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from discovery.flatjson import parse_flat
from discovery.cache import ThumbnailCache
from discovery.capture import AutoCapture

class Resources(unittest.TestCase):
    def test_stale_thumbnail_refreshes_are_bounded_and_corruption_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=ThumbnailCache(root,fetch=lambda _: (b'original','image/jpeg'))
            urls=[f'https://image.civitai.com/x/y/original=true/{n}.jpeg' for n in range(12)]
            for url in urls:cache.get(url)
            for meta in root.glob('*.json'):
                meta.write_text(json.dumps({'contentType':'image/jpeg','cachedAt':0}))
            release=threading.Event()
            def slow(url):release.wait(5);return b'new','image/jpeg'
            cache.fetch=slow
            try:
                for url in urls:self.assertEqual(cache.get(url)[0],b'original')
                self.assertEqual(len(cache.pending),6)
            finally:release.set()
            for _ in range(200):
                if not cache.pending:break
                time.sleep(.01)
            self.assertFalse(cache.pending)
            # A syntactically valid but corrupt timestamp must cause a safe miss.
            for meta in root.glob('*.json'):meta.write_text('{"contentType":"image/jpeg","cachedAt":"bad"}')
            cache.fetch=lambda _: (b'recovered','image/jpeg')
            self.assertEqual(cache.get(urls[0])[0],b'recovered')

    def test_retry_sleep_does_not_own_shared_request_lock(self):
        from discovery import history
        with tempfile.TemporaryDirectory() as temp:
            archive=history.HistoryArchive(Path(temp)/'history')
            archive._wait_api_lane=lambda *args:0
            acquired=[]
            def delay(*args):
                entered=threading.Event()
                def other_job():
                    with archive.api_lock:entered.set()
                worker=threading.Thread(target=other_job,daemon=True);worker.start()
                acquired.append(entered.wait(.5));worker.join(timeout=.5)
            error=urllib.error.HTTPError('https://isolated.invalid',503,'synthetic',{},io.BytesIO())
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.read.return_value=b'{"items":[]}';response.headers={}
            with patch.object(history.urllib.request,'urlopen',side_effect=[error,response]),patch.object(history.time,'sleep'),patch.object(history.random,'uniform',return_value=0):
                archive._request({},on_delay=delay,request_attempts=2)
            self.assertEqual(acquired,[True])

    def test_flattened_payload_limits_and_shared_values(self):
        self.assertEqual(parse_flat([{'left':1,'right':1,'flag':True},['Date','2026-01-01']]),{'left':'2026-01-01','right':'2026-01-01','flag':True})
        for payload in ([],[[0]],[{'x':9999}],[{'x':-99}],[[i+1] for i in range(80)]+['end'],[['Date']],[[1]]*100001):
            with self.assertRaises(ValueError):parse_flat(payload)
        with self.assertRaises(ValueError):parse_flat(' '*1048577)

    def test_thumbnail_eviction_preserves_other_data_and_lock_bookkeeping(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);calls=[]
            cache=ThumbnailCache(root,fetch=lambda url:(calls.append(url) or b'synthetic','image/jpeg'),max_entries=2,max_bytes=10000)
            (root/'account-snapshot.json').write_text('preserve')
            (root/'history.sqlite3').write_bytes(b'preserve')
            for i in range(12):cache.get(f'https://image.civitai.com/x/y/original=true/{i}.jpeg')
            gc.collect()
            self.assertLessEqual(len(list(root.glob('*.image'))),2)
            self.assertEqual(len(cache.first_locks),0)
            self.assertEqual((root/'account-snapshot.json').read_text(),'preserve')
            self.assertEqual((root/'history.sqlite3').read_bytes(),b'preserve')
            self.assertEqual(cache.get('https://image.civitai.com/x/y/original=true/11.jpeg')[0],b'synthetic')
            self.assertEqual(len(calls),12)

    def test_concurrent_thumbnail_first_fetch_is_single_flight(self):
        with tempfile.TemporaryDirectory() as temp:
            count=[0];started=threading.Event();release=threading.Event()
            def fetch(url):count[0]+=1;started.set();release.wait(5);return b'image','image/jpeg'
            cache=ThumbnailCache(Path(temp),fetch=fetch)
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures=[pool.submit(cache.get,'https://image.civitai.com/x/y/original=true/image.jpeg') for _ in range(8)]
                self.assertTrue(started.wait(1));release.set()
                self.assertTrue(all(f.result()[0]==b'image' for f in futures))
            self.assertEqual(count[0],1)

    def test_busy_retry_rechecks_without_mutating_recovery(self):
        now=datetime(2026,10,24,tzinfo=timezone.utc)
        entries={'2026-10-23#morning':{'next_retry_at':now.timestamp()-1}}
        fake=SimpleNamespace(recovery=SimpleNamespace(entries=lambda:entries),archive=SimpleNamespace(status=lambda _: {'state':'loading'}))
        target=AutoCapture._with_recovery_time(fake,now+timedelta(hours=12),{'autoCapture':True},now)
        self.assertEqual((target-now).total_seconds(),60)
        self.assertEqual(entries['2026-10-23#morning']['next_retry_at'],now.timestamp()-1)
        fake.archive.status=lambda _: {'state':'idle'}
        target=AutoCapture._with_recovery_time(fake,now+timedelta(hours=12),{'autoCapture':True},now)
        self.assertEqual((target-now).total_seconds(),1)

if __name__=='__main__':unittest.main()

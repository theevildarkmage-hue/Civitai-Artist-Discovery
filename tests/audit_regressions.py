"""Adversarial audit cases: no external connections or existing profile access."""
import test_safety
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import io,json,math,tempfile,threading,time,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch,Mock
from discovery import taste,history
from discovery.pagination import PageProgress,PaginationStopped
from discovery.local_security import static_relative_path
import server


def image(identity):
    return {'id':identity,'user':{'id':99,'username':'Artist'},'nsfwLevel':1,
            'reactions':[{'userId':42,'reaction':'Like'}],
            'tags':[{'id':11,'name':'landscape'}]}


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-audit-')
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def handler(self,path,headers=None):
        handler=server.Handler.__new__(server.Handler)
        handler.path=path;handler.headers=headers or {};handler.host_allowed=lambda:True
        handler.send_error=Mock()
        return handler

    def test_bad_static_paths_do_not_reach_resolution(self):
        for path in ('/%5c%5cattacker.invalid%5cshare%5cx','/C:%5cx','/..%2fx','/ui//x','/ui/./x','/%00x','//attacker.invalid/x','https://attacker.invalid/x'):
            handler=self.handler(path)
            with patch.object(Path,'resolve',side_effect=AssertionError('Untrusted filesystem resolution')):
                handler.do_GET()
            self.assertIn(handler.send_error.call_args.args[0],(400,403),path)
        self.assertEqual(static_relative_path('/ui/community.js'),'ui/community.js')
        self.assertEqual(static_relative_path('/'),'index.html')

    def test_cross_site_and_same_site_requests_stop_before_api_work(self):
        for site in ('cross-site','same-site'):
            handler=self.handler('/api/discovery/hidden?refresh=1',{'Sec-Fetch-Site':site})
            with patch.object(server,'auth_status',side_effect=AssertionError('Cross-site work')):
                handler.do_GET();handler.do_POST()
            self.assertEqual(handler.send_error.call_count,2)
            self.assertTrue(all(call.args==(403,) for call in handler.send_error.call_args_list))

    def test_locked_static_file_returns_controlled_error(self):
        handler=self.handler('/index.html')
        with patch.object(Path,'read_bytes',side_effect=PermissionError('locked')):
            handler.do_GET()
        handler.send_error.assert_called_once_with(503)

    def test_cursor_cycle_empty_sequence_and_request_cap(self):
        progress=PageProgress('A')
        progress.observe('B',True)
        with self.assertRaises(PaginationStopped):progress.observe('A',True)
        progress=PageProgress(page_limit=2)
        for cursor in ('A','B'):progress.before_request();progress.observe(cursor,True)
        with self.assertRaises(PaginationStopped):progress.before_request()
        progress=PageProgress()
        progress.observe('A',False);progress.observe('B',False)
        with self.assertRaises(PaginationStopped):progress.observe('C',False)

    def seed(self,count):
        store=taste.TasteStore(self.root/'taste')
        store._store_page([image(n) for n in range(1,count+1)],1,42)
        with store.connect() as db:
            store._set_state(db,'account_id',42);store._set_state(db,'sync_id',1)
        return store

    def sync(self,store,pages):
        with ExitStack() as stack:
            stack.enter_context(patch.object(taste,'auth_status',return_value={'id':42}))
            stack.enter_context(patch.object(store,'_page',side_effect=pages))
            for method in ('_pause','_sync_following','_sync_baseline','_sync_followers','_fetch_reacted_tags'):
                stack.enter_context(patch.object(store,method))
            store._run_sync()

    def data(self,store):
        with store.connect() as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('reacted_images','reacted_reactions','reacted_tags')}

    def test_empty_reaction_snapshot_preserves_rows_and_first_observation(self):
        store=self.seed(4);before=self.data(store)
        self.sync(store,[{'items':[],'nextCursor':None}])
        self.assertEqual(self.data(store),before)
        self.assertEqual(store.job['phase'],'error')
        with store.connect() as db:self.assertIsNone(store._state(db,'sync_checkpoint'))
        # A retry really re-fetches, instead of accepting the empty terminal checkpoint.
        self.sync(store,[{'items':[image(n) for n in range(1,5)],'nextCursor':None}])
        self.assertEqual(store.job['phase'],'complete')
        self.assertEqual(self.data(store)['reacted_images'][0][-3],before['reacted_images'][0][-3])

    def test_large_truncation_preserves_unseen_history_but_normal_removal_works(self):
        store=self.seed(30)
        self.sync(store,[{'items':[image(1)],'nextCursor':None}])
        self.assertEqual(len(self.data(store)['reacted_images']),30)
        self.assertEqual(store.job['phase'],'error')
        self.sync(store,[{'items':[image(n) for n in range(1,30)],'nextCursor':None}])
        self.assertEqual(len(self.data(store)['reacted_images']),29)
        self.assertEqual(store.job['phase'],'complete')

    def test_duplicate_items_cannot_disguise_truncation(self):
        store=self.seed(30)
        self.sync(store,[{'items':[image(1)]*30,'nextCursor':None}])
        self.assertEqual(len(self.data(store)['reacted_images']),30)
        self.assertEqual(store.job['phase'],'error')

    def test_reaction_cursor_loop_keeps_history(self):
        store=self.seed(4)
        self.sync(store,[{'items':[image(1)],'nextCursor':'same'},{'items':[image(2)],'nextCursor':'same'}])
        self.assertEqual(len(self.data(store)['reacted_images']),4)
        self.assertEqual(store.job['phase'],'error')

    def test_following_empty_and_truncated_snapshot_preserved(self):
        store=self.seed(0);client=Mock()
        with store.connect() as db:db.executemany('INSERT INTO followed_creators(creator_id) VALUES(?)',[(n,) for n in range(1,31)])
        for ids in ([],[1]):
            client.query.return_value=ids
            with self.assertRaises(taste.IncompleteProfileSnapshot):store._sync_following(client)
            with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM followed_creators').fetchone()[0],30)

    def test_v1_repeated_cursor_keeps_last_good_checkpoint(self):
        archive=history.HistoryArchive(self.root/'history');day='2026-10-04';key=day+'#morning'
        start=datetime(2026,10,4,5,tzinfo=timezone.utc);end=start+timedelta(hours=12)
        archive.cancel_events[key]=threading.Event();archive.jobs[key]={'state':'loading'}
        with archive.connect() as db:
            db.execute('INSERT INTO days(day,complete,content_rating) VALUES(?,0,?)',(key,'Soft'))
            db.execute('INSERT INTO block_feeds(block_key,browsing_mask,complete,scan_cursor) VALUES(?,1,0,?)',(key,'A'))
        row={'id':1,'type':'image','url':'https://isolated.invalid/image','createdAt':(start+timedelta(hours=1)).isoformat(),'user':{'username':'Artist'},'nsfwLevel':1}
        with patch.object(archive,'feed_floor',return_value=None),patch.object(archive,'_request',side_effect=[({'items':[row],'metadata':{'nextCursor':'B'}},100),({'items':[row],'metadata':{'nextCursor':'B'}},100)]) as request:
            archive._collect_v1(key,day,start,end)
        self.assertEqual(request.call_count,2);self.assertEqual(archive.jobs[key]['state'],'error')
        with archive.connect() as db:
            saved=db.execute('SELECT scan_cursor,pages,complete FROM block_feeds WHERE block_key=? AND browsing_mask=1',(key,)).fetchone()
            self.assertEqual(tuple(saved),('B',1,0))

    def test_same_block_start_reserves_only_one_worker(self):
        archive=history.HistoryArchive(self.root/'history');started=threading.Event();release=threading.Event();calls=[]
        def collect(*args):calls.append(args);started.set();release.wait(5)
        archive._collect=collect
        barrier=threading.Barrier(3)
        def begin():barrier.wait();archive.start('2026-10-04','2026-10-04T05:00:00Z','2026-10-04T17:00:00Z','America/Chicago','morning')
        workers=[threading.Thread(target=begin) for _ in range(2)]
        for worker in workers:worker.start()
        barrier.wait()
        for worker in workers:worker.join(5);self.assertFalse(worker.is_alive())
        self.assertTrue(started.wait(2));self.assertEqual(len(calls),1);release.set()

    def test_retry_after_nonfinite_fallback_and_huge_wait_stop(self):
        archive=history.HistoryArchive(self.root/'history')
        archive._wait_api_lane=lambda *args:0
        for method,args in ((archive._request,{}),(archive._search_request,{})):
            for retry in ('inf','nan','9999999999'):
                error=urllib.error.HTTPError('https://isolated.invalid',429,'limited',{'Retry-After':retry},io.BytesIO(b''))
                with patch.object(history.urllib.request,'urlopen',side_effect=error),patch.object(history.time,'sleep') as sleep:
                    with self.assertRaises(history.RetryBudgetExhausted):method(args)
                self.assertTrue(all(math.isfinite(call.args[0]) and call.args[0]<=602 for call in sleep.call_args_list))
                if retry=='9999999999':sleep.assert_not_called()


if __name__=='__main__':unittest.main()

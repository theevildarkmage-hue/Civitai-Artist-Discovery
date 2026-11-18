"""HTTP-level optional tag errors and durable preparation outcomes; isolated synthetic I/O."""
import test_safety
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import social,taste

def missing(path='tag.getVotableTags',status=404,code='NOT_FOUND'):
    return {'error':{'json':{'message':'Synthetic missing artwork','data':{'path':path,'httpStatus':status,'code':code}}}}

def value(tags):return {'result':{'data':{'json':tags}}}

class Transport(unittest.TestCase):
    def lookup(self,body,status=404,procedure='tag.getVotableTags',optional=True):
        payload=json.dumps(body).encode() if not isinstance(body,bytes) else body
        def fail(request,**kwargs):
            raise urllib.error.HTTPError(request.full_url,status,'Synthetic status',{},io.BytesIO(payload))
        with patch.object(social.urllib.request,'urlopen',side_effect=fail),patch.object(social,'get_access_token',return_value='synthetic'),patch.object(social,'refresh_access_token',side_effect=RuntimeError('Synthetic revoked')),patch.object(social.time,'sleep'):
            method=social.SocialClient().batch_query_optional if optional else social.SocialClient().batch_query
            return method(procedure,[{'id':i+1,'type':'image'} for i in range(max(1,len(body)) if isinstance(body,list) else 1)])
    def test_all_missing_and_mixed_http_404(self):
        self.assertEqual(self.lookup([missing(),missing()]),[None,None])
        tags=[{'id':1,'name':'landscape'}]
        self.assertEqual(self.lookup([value(tags),missing(),value([])]),[tags,None,[]])
    def test_invalid_http_404_is_not_missing(self):
        for body in (b'Not found',{},[],[missing(path='user.getCreator')],[{'error':None}],
                     [missing(status=403,code='FORBIDDEN')],[value([])]):
            with self.subTest(body=body),self.assertRaises(social.CivitaiHTTPError):self.lookup(body)
    def test_unrelated_and_strict_procedures_do_not_swallow_404(self):
        for procedure,optional in (('user.getCreator',True),('tag.getVotableTags',False)):
            with self.subTest(procedure=procedure),self.assertRaises(social.CivitaiHTTPError):
                self.lookup([missing()],procedure=procedure,optional=optional)
    def test_non_404_failures_propagate(self):
        for status in (401,403,429,500,503):
            with self.subTest(status=status),self.assertRaises(Exception):self.lookup([missing()],status=status)
    def test_null_success_and_contradictory_auth_error_are_not_unavailable(self):
        client=social.SocialClient()
        for response in ([value(None)],[missing(status=403,code='NOT_FOUND')]):
            with patch.object(client,'_request',return_value=response),self.assertRaises(Exception):
                client.batch_query_optional('tag.getVotableTags',[{'id':1,'type':'image'}])

class ImmediateCancel(threading.Event):
    def wait(self,timeout=None):return self.is_set()

class Outcomes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='civitai-tag-outcomes-');self.root=Path(self.temp.name)
        self.store=taste.TasteStore(self.root);self.account=[7];self.now=[1000.0]
        self.patches=[patch.object(taste,'auth_status',side_effect=lambda:{'connected':True,'id':self.account[0]}),
                      patch.object(taste.time,'time',side_effect=lambda:self.now[0])]
        for item in self.patches:item.start()
        self.cancel=ImmediateCancel()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def client(self,results):
        class Client:
            calls=[]
            def batch_query_optional(inner,procedure,payloads):
                self.assertEqual(procedure,'tag.getVotableTags');inner.calls.append(payloads)
                return results(payloads) if callable(results) else results
        return Client()
    def test_mixed_outcomes_restart_and_retry_preserve_completed_tags(self):
        client=self.client([[{'name':'landscape'}],None,[]])
        self.assertEqual(self.store.sweep_image_tags(client,[1,2,3],self.cancel),3)
        self.assertEqual(self.store.tag_coverage([1,2,3]),(2,3))
        state=self.store.tag_preparation_coverage([1,2,3])
        self.assertEqual((state['known'],state['unavailable'],state['processed'],state['pending'],state['complete']),(2,1,3,0,True))
        restarted=taste.TasteStore(self.root)
        self.assertTrue(restarted.tag_preparation_coverage([1,2,3])['complete'])
        self.assertEqual(restarted.sweep_image_tags(client,[1,2,3],self.cancel),0)
        self.assertEqual(len(client.calls),1)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT tag_name FROM archive_image_tags WHERE image_id=1').fetchone()[0],'landscape')
            row=db.execute('SELECT reason,observed_at,retry_after FROM archive_tag_unavailable').fetchone()
            self.assertEqual(row[0],'not_found');self.assertTrue(row[1]);self.assertEqual(row[2],87400.0)
    def test_expiry_rechecks_only_unavailable_and_clears_recovered_outcome(self):
        self.store.sweep_image_tags(self.client([[],None]),[1,2],self.cancel)
        self.now[0]+=taste.TAG_UNAVAILABLE_RECHECK_SECONDS+1
        self.assertFalse(self.store.tag_preparation_coverage([1,2])['complete'])
        recovered=self.client([[]]);self.store.sweep_image_tags(recovered,[1,2],self.cancel)
        self.assertEqual(recovered.calls,[[{'id':2,'type':'image'}]])
        self.assertEqual(self.store.tag_preparation_coverage([1,2])['unavailable'],0)
        self.assertEqual(self.store.tag_coverage([1,2]),(2,2))
    def test_missing_visibility_is_account_scoped(self):
        self.store.sweep_image_tags(self.client([None]),[1],self.cancel)
        self.account[0]=8
        self.assertEqual(self.store.tag_preparation_coverage([1])['unavailable'],0)
        client=self.client([[]]);self.store.sweep_image_tags(client,[1],self.cancel)
        self.assertEqual(len(client.calls),1)
    def test_prior_chunks_survive_transient_failure_and_retry(self):
        def fail_second(payloads):
            if payloads[0]['id']==101:raise social.CivitaiHTTPError(503,'Synthetic outage')
            return [[] for item in payloads]
        with patch.object(taste,'MAX_ATTEMPTS',1),self.assertRaises(social.CivitaiHTTPError):
            self.store.sweep_image_tags(self.client(fail_second),list(range(1,102)),self.cancel)
        self.assertEqual(self.store.tag_coverage(list(range(1,102))),(100,101))
        self.assertEqual(self.store.tag_preparation_coverage([101])['unavailable'],0)
        client=self.client([[]]);self.store.sweep_image_tags(client,list(range(1,102)),self.cancel)
        self.assertEqual(client.calls,[[{'id':101,'type':'image'}]])
    def test_invalid_batch_does_not_persist_missing(self):
        for results in ([],[{}],[None,None]):
            with self.subTest(results=results),self.assertRaises(RuntimeError):
                self.store.sweep_image_tags(self.client(results),[1],self.cancel)
        self.assertEqual(self.store.tag_preparation_coverage([1])['processed'],0)
    def test_status_does_not_trust_in_memory_attempted_all_after_expiry(self):
        import server
        self.store.sweep_image_tags(self.client([None]),[1],self.cancel)
        class History:
            def day_artist_keys(inner,key):return [{'representativeId':1}]
        with patch.object(server,'HISTORY',History()),patch.object(server,'TASTE',self.store):
            server.SWEEP_JOBS['tags'].update(running=False,day='synthetic',error=None,attemptedAll=True)
            self.assertTrue(server.sweep_status('tags','synthetic')['complete'])
            self.now[0]+=taste.TAG_UNAVAILABLE_RECHECK_SECONDS+1
            self.assertFalse(server.sweep_status('tags','synthetic')['complete'])

if __name__=='__main__':unittest.main()

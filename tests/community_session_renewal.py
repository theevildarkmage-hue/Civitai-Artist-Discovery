"""Protected storage and controlled 72-hour operation; provider/backend are synthetic."""
import test_safety
import base64,copy,json,os,secrets,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from discovery import oauth,cloud_identity,device_identity
from discovery.community import CommunityError
from discovery.capture import AutoCapture
from discovery.history import HistoryArchive,LOCAL_ZONE
from discovery.settings import AppSettings

class RenewalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='data-test-renewal-',ignore_cleanup_errors=True)
  self.root=Path(self.temp.name);self.now=[1800000000.0];self.attempts=0
  self.patches=[patch.object(oauth,'APP_DATA',self.root),patch.object(oauth,'TOKEN_PATH',self.root/'oauth_tokens.dpapi'),
   patch.object(oauth,'LEGACY_TOKEN_PATH',self.root/'oauth_tokens.dpapi'),patch.object(oauth,'client_id',return_value='synthetic-client'),
   patch.object(oauth.time,'time',side_effect=lambda:self.now[0]),patch.object(oauth.webbrowser,'open',side_effect=AssertionError('No interactive login allowed'))]
  for p in self.patches:p.start()
  oauth._CLOUD_RETRY.clear()
  self.device=device_identity.generate()
  self.primary={'access_token':'synthetic-local-access','refresh_token':'synthetic-local-refresh','scope':917539,'client_id':'synthetic-client',
   'identity':{'id':7,'username':'Synthetic'},'expires_at':int(self.now[0])+3600,'discovery_session':self.session()}
  oauth._save(self.primary)
 def tearDown(self):
  oauth._CLOUD_RETRY.clear()
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def session(self):
  return {'session':'sdc_'+secrets.token_urlsafe(32),'member_id':7,'expires_at':int(self.now[0])+43200,
   'renewal':{'credential':'sdr_'+secrets.token_urlsafe(32),'device':self.device,'expires_at':int(self.now[0])+30*86400,'absolute_expires_at':1800000000+90*86400}}
 def renew(self,current):
  self.attempts+=1;self.assertIn('pending_request',current['renewal']);return self.session()
 def expire(self):
  value=oauth._load();value['discovery_session']['expires_at']=int(self.now[0])-1;oauth._save(value)
 def test_72_hours_multiple_expiries_daily_capture_and_outage_recovery(self):
  calls=[];root=self.root;clock=self.now
  class Archive:
   def __init__(self):self.root=root;self.complete=set()
   archive_key=staticmethod(HistoryArchive.archive_key)
   def history_window(self,rating):return {'oldestBuildableDay':(datetime.fromtimestamp(clock[0],LOCAL_ZONE).date()-timedelta(days=2)).isoformat()}
   def status(self,key):return {'state':'complete' if key in self.complete else 'not_started','complete':key in self.complete}
   def start(self,day,start,end,zone,segment,rating):
    calls.append(oauth.get_access_token());self.complete.add(self.archive_key(day,segment))
  archive=Archive();settings=AppSettings(self.root/'settings.json');settings.update(auto_capture_value=True,auto_capture_hours_value=12)
  capture=AutoCapture(archive,settings,now=lambda:datetime.fromtimestamp(self.now[0],LOCAL_ZONE))
  def provider(name,body):
   self.assertEqual(name,'token');self.assertEqual(body['grant_type'],'refresh_token')
   return {'access_token':'synthetic-access-'+str(int(self.now[0])),'refresh_token':'synthetic-refresh-'+str(int(self.now[0])),'expires_in':3600}
  def backend(current):
   if 24<=int((self.now[0]-1800000000)/3600)<30:self.attempts+=1;raise OSError('Synthetic Supabase outage')
   return self.renew(current)
  failures=0
  with patch.object(oauth,'_post',side_effect=provider),patch.object(cloud_identity,'renew',side_effect=backend):
   for hour in range(73):
    self.now[0]=1800000000+hour*3600
    oauth.get_access_token()
    try:oauth.get_community_session()
    except oauth.OAuthTemporarilyUnavailable:failures+=1
    result=capture.run_once(record_empty=False);self.assertFalse(result['failed'])
  self.assertGreaterEqual(len(archive.complete),6);self.assertGreaterEqual(self.attempts,6);self.assertGreater(failures,0)
  self.assertEqual(oauth._load()['identity']['id'],7);self.assertFalse(oauth._load().get('reauth_required',False))
  self.assertTrue(calls)
 def test_outage_backoff_and_daily_independence(self):
  self.expire()
  with patch.object(cloud_identity,'renew',side_effect=OSError('Synthetic outage')) as renew:
   for _ in range(50):
    with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.get_community_session()
   self.assertEqual(renew.call_count,1)
  self.assertEqual(oauth.get_access_token(),'synthetic-local-access')
  self.now[0]+=21
  with patch.object(cloud_identity,'renew',side_effect=self.renew):self.assertTrue(oauth.get_community_session().startswith('sdc_'))
 def test_revoked_community_family_does_not_sign_out_civitai(self):
  self.expire()
  with patch.object(cloud_identity,'renew',side_effect=CommunityError('AUTH_EXPIRED',401)) as renew:
   for _ in range(3):
    with self.assertRaises(oauth.OAuthReauthenticationRequired):oauth.get_community_session()
   self.assertEqual(renew.call_count,1)
  self.assertEqual(oauth.get_access_token(),'synthetic-local-access');self.assertFalse(oauth._load().get('reauth_required',False))
 def test_lost_reply_preserves_rotation_id_for_recovery(self):
  self.expire();ids=[]
  def lost(value):ids.append(value['renewal']['pending_request']);raise OSError('Reply lost after commit')
  with patch.object(cloud_identity,'renew',side_effect=lost):
   with self.assertRaises(oauth.OAuthTemporarilyUnavailable):oauth.get_community_session()
  oauth._CLOUD_RETRY.clear()
  def retry(value):ids.append(value['renewal']['pending_request']);return self.renew(value)
  with patch.object(cloud_identity,'renew',side_effect=retry):oauth.get_community_session()
  self.assertEqual(ids[0],ids[1]);self.assertNotIn('pending_request',oauth._load()['discovery_session']['renewal'])
 def test_renewal_does_not_hold_civitai_refresh_lock_or_lose_primary_rotation(self):
  self.expire();entered=threading.Event();release=threading.Event();errors=[];sessions=[]
  def slow(value):entered.set();release.wait(3);return self.renew(value)
  def worker():
   try:sessions.append(oauth.get_community_session())
   except Exception as e:errors.append(type(e).__name__)
  with patch.object(cloud_identity,'renew',side_effect=slow):
   thread=threading.Thread(target=worker);thread.start();self.assertTrue(entered.wait(1))
   started=time.monotonic();self.assertEqual(oauth.get_access_token(),'synthetic-local-access');self.assertLess(time.monotonic()-started,1)
   with oauth._REFRESH_LOCK:
    value=oauth._load();value['refresh_token']='synthetic-new-primary-rotation';oauth._save(value)
   waiting=threading.Thread(target=worker);waiting.start()
   release.set();thread.join(3);waiting.join(3)
  self.assertEqual(len(sessions),2);self.assertEqual(sessions[0],sessions[1])
  self.assertFalse(errors);self.assertEqual(oauth._load()['refresh_token'],'synthetic-new-primary-rotation')
 def test_restart_uses_protected_existing_grant_and_renews_without_browser(self):
  self.expire()
  code="""import json,time
from discovery import oauth,cloud_identity
oauth.client_id=lambda:'synthetic-client'
oauth.time.time=lambda:1800000000
oauth.webbrowser.open=lambda *a:(_ for _ in ()).throw(AssertionError('No browser'))
before=oauth._load()
def renew(value):
 assert 'pending_request' in value['renewal']
 return {**value,'session':'sdc_'+'r'*43,'expires_at':int(time.time())+43200}
cloud_identity.renew=renew
assert oauth.get_community_session()=='sdc_'+'r'*43
after=oauth._load()
assert after['access_token']==before['access_token'] and after['refresh_token']==before['refresh_token']
print('PASS: isolated protected-store child restart; local primary unchanged; silent Community renewal')
"""
  result=subprocess.run([sys.executable,'-c',code],cwd=test_safety.ROOT,env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(self.root)},capture_output=True,text=True,timeout=20)
  self.assertEqual(result.returncode,0,result.stderr);self.assertIn('PASS:',result.stdout)

if __name__=='__main__':unittest.main()

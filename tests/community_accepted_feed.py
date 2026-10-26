import test_safety
import copy,time,unittest
from datetime import datetime,timezone
from unittest.mock import Mock
from discovery.community import CommunityClient,CommunityError,ContentProfile
def row(i):return dict(imageId=i,creatorId=i+1000,recommender='Synthetic',submittedAt=datetime.now(timezone.utc).isoformat())
class Backend:
 def __init__(self,n=125):self.rows=[row(i) for i in range(n,0,-1)];self.calls=[];self.delay=0;self.failure=None
 def request(self,op,token,payload):
  assert token is None;self.calls.append((op,copy.deepcopy(payload)));time.sleep(self.delay)
  if self.failure:raise CommunityError(self.failure,503)
  if op=='status':return dict(apiVersion=1,member={'id':17},quota={'remaining':3},beta={'available':True,'submissions':False})
  c=payload.get('cursor');n=payload.get('limit',50);rows=[r for r in self.rows if c is None or r['imageId']<int(c)]
  return dict(apiVersion=1,feedVersion=3,items=copy.deepcopy(rows[:n]),nextCursor=str(rows[n-1]['imageId']) if len(rows)>n else None)
def make_client(n=125,profile=None):
 b=Backend(n);v=Mock();v.artwork.side_effect=AssertionError('List invoked artwork verifier')
 c=CommunityClient(b,v,lambda:(_ for _ in ()).throw(AssertionError('List fetched OAuth')),lambda _:profile or ContentProfile())
 c.mode='supabase';c.identity_provider=lambda:17;return c,b,v
class Contract(unittest.TestCase):
 def test_pagination(self):
  for n in (0,3,13,24,50,51,125):
   c,b,v=make_client(n);seen=[];cursor=None
   while True:
    p=c.request('gallery',{'cursor':cursor});seen.extend(x['imageId'] for x in p['items']);cursor=p['nextCursor']
    if cursor is None:break
   self.assertEqual(seen,list(range(n,0,-1)));v.artwork.assert_not_called()
 def test_preferences_local(self):
  c,b,v=make_client(profile=ContentProfile(tags=frozenset({'private'})));self.assertEqual(len(c.request('gallery',{})['items']),50);self.assertNotIn('filters',b.calls[0][1]);self.assertNotIn('artwork',b.rows[0])
 def test_account(self):
  c,b,v=make_client();c.request('gallery',{});self.assertTrue(c.listed(125));c.identity_provider=lambda:18;self.assertFalse(c.listed(125))
 def test_denial(self):
  c,b,v=make_client();c.request('gallery',{});b.failure='INVITE_REVOKED'
  with self.assertRaises(CommunityError):c.request('gallery',{})
  self.assertFalse(c.accepted)
 def test_old_protocol(self):
  c,b,v=make_client();b.request=lambda *a:dict(apiVersion=1,feedVersion=2,items=[])
  with self.assertRaises(CommunityError):c.request('gallery',{})
if __name__=='__main__':unittest.main()

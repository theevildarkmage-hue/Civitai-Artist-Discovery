"""Rating filters hide previews/gallery; they do not reject valid submissions."""
import test_safety
from dataclasses import replace
from pathlib import Path
import tempfile,unittest
from discovery.community import CommunityClient,ContentProfile,FixtureVerifier,LocalCommunityBackend,CommunityError,RateLimiter

class RatingSubmission(unittest.TestCase):
 def make(self,level=4,kind='image',profile=None):
  temp=tempfile.TemporaryDirectory(prefix='cad3-rating-');self.addCleanup(temp.cleanup)
  verifier=FixtureVerifier(development=True)
  verifier.artworks[1001]=replace(verifier.artworks[1001],rating=level,media_type=kind)
  backend=LocalCommunityBackend(Path(temp.name)/'community.sqlite3',verifier);backend.rate=RateLimiter(1000)
  client=CommunityClient(backend,verifier,lambda:'fixture:1',lambda _:profile or ContentProfile())
  return client,backend,verifier

 def test_every_rating_and_media_preserves_viewer_filters(self):
  for level in (1,2,4,8,16):
   for kind in ('image','video'):
    with self.subTest(level=level,kind=kind):
     client,backend,verifier=self.make(level,kind)
     result=client.request('verify',{'artwork':1001})
     self.assertEqual(result['quota']['used'],0)
     if level not in (1,2):
      self.assertTrue(result['previewHidden'])
      self.assertEqual(result['artwork'],{'id':1001,'type':kind})
      self.assertNotIn('thumbnail',str(result));self.assertNotIn('image.civitai.com',str(result))
     else:self.assertIn('thumbnailUrl',result['artwork'])
     client.request('recommend',{'artwork':1001})
     self.assertEqual(len(client.request('gallery',{})['items']),int(level in (1,2)))
     other=CommunityClient(backend,verifier,lambda:'fixture:2',lambda _:ContentProfile(levels=frozenset((1,2,4,8,16))))
     self.assertEqual(len(other.request('gallery',{})['items']),1)

 def test_other_content_controls_remain_enforced(self):
  for profile in (ContentProfile(images=frozenset((1001,))),ContentProfile(creators=frozenset((101,))),
                  ContentProfile(usernames=frozenset(('demoartist1',))),ContentProfile(tags=frozenset(('landscape',))),
                  ContentProfile(tag_ids=frozenset((11,))),ContentProfile(blocked_creators=frozenset((101,))),
                  ContentProfile(blocked_usernames=frozenset(('demoartist1',)))):
   client,backend,_=self.make(profile=profile)
   for operation in ('verify','recommend'):
    with self.assertRaises(CommunityError) as error:client.request(operation,{'artwork':1001})
    self.assertEqual(error.exception.code,'CONTENT_FILTERED')
   self.assertEqual(backend.request('status','fixture:1',{})['quota']['used'],0)

if __name__=='__main__':unittest.main()

"""Community video metadata, old-cache compatibility and image-only poster cache."""
import test_safety
import json,subprocess,tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from discovery.community import Artwork,CommunityClient,CommunityError,ContentProfile
from discovery.cache import MetadataCache,ArtworkCache,ThumbnailCache,thumbnail_source

class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-community-video-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.art=Artwork(123,42,'Artist',1,'https://image.civitai.com/key/id/original=true/clip.mp4',media_type='video')

    def test_poster_cache_and_player_urls_remain_separate(self):
        art=self.art;value=CommunityClient.browser_artwork(art)
        self.assertEqual('/api/community/thumbnail/123',value['thumbnailUrl'])
        self.assertEqual('video',value['type']);self.assertIn('/transcode=true,width=450,optimized=true/',value['videoUrl'])
        self.assertIn('/transcode=true,width=768,optimized=true/',value['detailVideoUrl'])
        calls=[]
        cache=ThumbnailCache(self.root/'thumbs',fetch=lambda url:(calls.append(url) or b'synthetic-still','image/jpeg'))
        cache.get(art.preview,video=True);cache.get(art.preview,video=True)
        self.assertEqual(['https://image.civitai.com/key/id/anim=false,width=512/clip.mp4'],calls)
        self.assertNotEqual(thumbnail_source(art.preview),thumbnail_source(art.preview,video=True))
        for url in ('http://image.civitai.com/key/width=640/a.mp4','https://evil.invalid/key/width=640/a.mp4','https://user@image.civitai.com/key/width=640/a.mp4','https://image.civitai.com:443/key/width=640/a.mp4'):
            with self.assertRaises(ValueError):thumbnail_source(url,video=True)

    def test_old_image_cache_and_video_restart_preserved(self):
        store=MetadataCache(self.root/'metadata.sqlite3')
        class Verifier:
            def artwork(self,image):raise AssertionError('Fresh cache must not contact provider')
        cache=ArtworkCache(store,Verifier(),'hosted')
        old=asdict(self.art);old.pop('media_type');old['preview']='https://image.civitai.com/key/id/width=512/art.jpeg'
        store.put(cache.key(1),old)
        self.assertEqual('image',cache.browse(1)[0].media_type)
        cache.remember(self.art)
        restarted=ArtworkCache(MetadataCache(self.root/'metadata.sqlite3'),Verifier(),'hosted')
        self.assertEqual(self.art,restarted.browse(123)[0])
        client=CommunityClient(None,Verifier(),lambda:'synthetic',lambda token:ContentProfile())
        client.artwork_cache=restarted
        detail=client.request('detail',{'artwork':123})
        self.assertEqual('video',detail['type']);self.assertIn('width=768',detail['detailVideoUrl'])
        self.assertFalse(ContentProfile(levels=frozenset((2,))).allows(self.art))
        self.assertFalse(ContentProfile(images=frozenset((123,))).allows(self.art))

    def test_edge_video_eligibility(self):
        subprocess.run(['node','--experimental-transform-types',str(test_safety.ROOT/'tests/community_video_edge.mjs')],check=True)

if __name__=='__main__':unittest.main()

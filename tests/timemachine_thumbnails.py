"""Real local thumbnail HTTP routes, separate SQLite stores, synthetic CDN only."""
import test_safety
import io,json,threading,unittest,urllib.request,urllib.error
from contextlib import ExitStack
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer
from PIL import Image
import server
from discovery.history import HistoryArchive
from discovery.timemachine import TimeMachine
from discovery.taste import TasteStore
from discovery.cache import ThumbnailCache

class ThumbnailRoutes(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  import tempfile
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cad3-thumbnail-')))
  self.history=HistoryArchive(self.root/'history');self.taste=TasteStore(self.root/'taste')
  self.machine=TimeMachine(self.root/'machine',self.history,self.taste)
  self.url='https://image.civitai.com/key/art/original=true/art.jpeg'
  self.history._upsert_normalized([{'id':1,'username':'Daily','url':self.url,'type':'image','createdAt':'2026-11-07T12:00:00Z','browsingLevel':1},
                                  {'id':3,'username':'Daily','url':self.url,'type':'image','createdAt':'2026-11-07T12:00:00Z','browsingLevel':1}])
  with self.machine.connect() as db:
   for i,level,kind in ((2,1,'image'),(3,4,'image'),(4,1,'video')):
    db.execute('INSERT INTO creator_images(username_key,position,image_id,username,created_at,url,browsing_level,type) VALUES(?,?,?,?,?,?,?,?)',
               ('machine',i,i,'Machine','2026-11-07T12:00:00Z',self.url if kind=='image' else self.url.replace('.jpeg','.mp4'),level,kind))
  self.snapshot=self.saved()
  png=io.BytesIO();Image.new('RGB',(8,8),(60,100,160)).save(png,'PNG');self.png=png.getvalue()
  self.fetches=[];self.failure=None;self.hidden=(set(),set());self.tags={'known':True,'tags':[]};self.hidden_tags=set();self.account=42
  def fetch(url):
   self.fetches.append(url)
   if self.failure:raise self.failure
   return self.png,'image/png'
  for name,value in {'HISTORY':self.history,'TIME_MACHINE':self.machine,'TASTE':self.taste,
                     'PERSONAL_THUMBNAILS':ThumbnailCache(self.root/'cache',fetch=fetch)}.items():
   self.stack.enter_context(patch.object(server,name,value))
  self.stack.enter_context(patch.object(server,'auth_status',side_effect=lambda:{'id':self.account}))
  self.stack.enter_context(patch.object(server,'hidden_preferences',side_effect=lambda:self.hidden))
  self.stack.enter_context(patch.object(self.taste,'image_tags_many',side_effect=lambda ids:{i:self.tags for i in ids}))
  self.stack.enter_context(patch.object(self.taste,'hidden_tag_names',side_effect=lambda:self.hidden_tags))
  self.stack.enter_context(patch.object(server.SETTINGS,'load',return_value={'browsingLevels':[1]}))
  self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
  self.stack.enter_context(patch.object(server,'RUNNING_PORT',self.http.server_port))
  threading.Thread(target=self.http.serve_forever,daemon=True).start()
  self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
  self.base=f'http://127.0.0.1:{self.http.server_port}'

 def saved(self):
  with self.machine.connect() as db:
   return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ('creator_images','creator_progress')}

 def get(self,path):
  try:
   with urllib.request.urlopen(self.base+path) as response:return response.status,response.read()
  except urllib.error.HTTPError as error:return error.code,error.read()

 def tearDown(self):
  self.assertEqual(self.snapshot,self.saved(),'preview requests must preserve records, cursor and position')

 def test_store_scoping_overlap_and_real_decoding(self):
  self.assertEqual(self.get('/api/timemachine/thumbnail/2'),(200,self.png))
  self.assertEqual(self.get('/api/history/thumbnail/2')[0],404)
  self.assertEqual(self.get('/api/history/thumbnail/1')[0],200)
  self.assertEqual(self.get('/api/timemachine/thumbnail/1')[0],404)
  # Overlap must use the requested store's own rating, never the permissive copy.
  self.assertEqual(self.get('/api/history/thumbnail/3')[0],200)
  self.assertEqual(self.get('/api/timemachine/thumbnail/3')[0],403)
  Image.open(io.BytesIO(self.get('/api/timemachine/thumbnail/2')[1])).load()
  with self.history.connect() as db:self.assertIsNone(db.execute('SELECT 1 FROM images WHERE id=2').fetchone())

 def test_unknown_unauthenticated_hidden_and_tags_never_fetch(self):
  for path,status in (('/api/timemachine/thumbnail/999',404),('/api/timemachine/thumbnail/4',404)):
   self.assertEqual(self.get(path)[0],status)
  self.account=None;self.assertEqual(self.get('/api/timemachine/thumbnail/2')[0],401);self.account=42
  for hidden,tags,names in ((({'machine'},set()),self.tags,set()),((set(),{2}),self.tags,set()),
                            ((set(),set()),{'known':False,'tags':[]},{'hidden'}),
                            ((set(),set()),{'known':True,'tags':[{'hidden':True}]},{'hidden'})):
   self.hidden=hidden;self.tags=tags;self.hidden_tags=names
   self.assertEqual(self.get('/api/timemachine/thumbnail/2')[0],403)
  self.assertEqual(self.fetches,[])

 def test_upstream_errors_remain_retryable_not_missing(self):
  for error in (urllib.error.HTTPError(self.url,403,'synthetic',{},None),
                urllib.error.HTTPError(self.url,429,'synthetic',{},None),TimeoutError('synthetic'),ValueError('decode failed')):
   self.failure=error;status,body=self.get('/api/timemachine/thumbnail/2')
   self.assertEqual(status,503);self.assertEqual(json.loads(body)['code'],'UNAVAILABLE')
  self.failure=None;self.assertEqual(self.get('/api/timemachine/thumbnail/2')[0],200)

 def test_decoration_keeps_store_and_video_poster_semantics(self):
  art={'id':2,'type':'image','url':self.url}
  self.assertEqual(server.attach_preview_tags([{'representative':art}],source='timemachine')[0]['representative']['url'],'/api/timemachine/thumbnail/2')
  self.assertEqual(server.personal_preview(art)['url'],'/api/history/thumbnail/2')
  video={'id':4,'type':'video','url':self.url.replace('.jpeg','.mp4'),'thumbnailUrl':self.url}
  self.assertEqual(server.personal_preview(video,source='timemachine'),video)

if __name__=='__main__':unittest.main()

"""Instance publication survives Windows readers without nonatomic fallback."""
import test_safety
import ctypes,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import server

class InstancePublication(unittest.TestCase):
 def setUp(self):
  self.temporary=tempfile.TemporaryDirectory(prefix='cad3-instance-file-');self.root=Path(self.temporary.name)
  self.path=self.root/'running-instance.json';self.old={'url':'http://127.0.0.1:34560','pid':0}
  self.path.write_text(json.dumps(self.old));self.url='http://127.0.0.1:34561'
  self.patches=[patch.object(server,'DATA_ROOT',self.root),patch.object(server,'INSTANCE_FILE',self.path)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temporary.cleanup()
 def test_transient_denial_keeps_old_instance_until_atomic_publication(self):
  original=os.replace;attempts=[]
  def replace(source,target):
   attempts.append(1)
   self.assertEqual(json.loads(self.path.read_text()),self.old)
   if len(attempts)<3:raise PermissionError('Synthetic sharing contention')
   return original(source,target)
  with patch('discovery.atomic_file.os.replace',side_effect=replace),patch('discovery.atomic_file.time.sleep'):
   server.save_instance_url(self.url)
  self.assertEqual(len(attempts),3);self.assertEqual(json.loads(self.path.read_text()),{'url':self.url,'pid':os.getpid()})
  self.assertFalse(list(self.root.glob('*.tmp')))
 def test_permanent_denial_is_bounded_preserves_old_instance_and_cleans_temp(self):
  with patch('discovery.atomic_file.os.replace',side_effect=PermissionError('Synthetic permanent denial')) as replace,patch('discovery.atomic_file.time.sleep') as sleep:
   with self.assertRaises(PermissionError):server.save_instance_url(self.url)
  self.assertEqual(replace.call_count,7);self.assertEqual(sleep.call_count,6)
  self.assertEqual(json.loads(self.path.read_text()),self.old);self.assertFalse(list(self.root.glob('*.tmp')))
 @unittest.skipUnless(os.name=='nt','Actual Windows sharing semantics')
 def test_actual_windows_reader_releases_during_instance_publication(self):
  api=ctypes.WinDLL('kernel32',use_last_error=True)
  api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
  api.CloseHandle.argtypes=[ctypes.c_void_p]
  handle=api.CreateFileW(str(self.path),0x80000000,3,None,3,0x80,None)
  self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
  def release():time.sleep(.12);api.CloseHandle(handle)
  thread=threading.Thread(target=release);thread.start()
  try:server.save_instance_url(self.url)
  finally:thread.join()
  self.assertEqual(json.loads(self.path.read_text()),{'url':self.url,'pid':os.getpid()})
  self.assertFalse(list(self.root.glob('*.tmp')))

if __name__=='__main__':unittest.main()

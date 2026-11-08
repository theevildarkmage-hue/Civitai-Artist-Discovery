"""Isolated real HTTP restart handler and actual replacement source processes; no accounts."""
import test_safety
import json,os,subprocess,sys,tempfile,time,threading,urllib.request
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

def scenario():
 import server
 from discovery.build_config import ENVIRONMENT,CHANNEL
 root=server.DATA_ROOT
 marker=root/'preserved-profile.txt';marker.write_text('synthetic profile sentinel',encoding='utf-8')
 # The initial server is managed in this process so its real Popen replacement
 # is registered by the existing network guard, with its exact PID/data root.
 http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);server.RUNNING_PORT=http.server_port
 thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
 base=f'http://127.0.0.1:{http.server_port}'
 children=[];original=subprocess.Popen
 def spawn(*args,**kwargs):
  # Release our managed listener before launch so the unchanged test safety
  # guard can prove the requested port is free. Production's bounded handover
  # wait is covered separately; never relax the occupied-port safety rule.
  http.shutdown();http.server_close()
  child=original(*args,**kwargs);children.append(child);return child
 try:
  with patch.object(server.subprocess,'Popen',side_effect=spawn):
   request=urllib.request.Request(base+'/api/app/restart',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
   with urllib.request.urlopen(request,timeout=5) as response:assert json.load(response)['restarting']
  thread.join(timeout=10);assert not thread.is_alive();http.server_close()
  assert len(children)==1;child=children[0]
  deadline=time.monotonic()+25
  while True:
   try:
    with urllib.request.urlopen(base+'/api/history/config',timeout=1) as response:config=json.load(response)
    break
   except (OSError,PermissionError):
    assert child.poll() is None,'Replacement exited'
    if time.monotonic()>deadline:raise AssertionError('Owned replacement did not become ready')
    time.sleep(.1)
  assert config['environment']==ENVIRONMENT,(ENVIRONMENT,config['environment'])
  assert config['channel']==CHANNEL
  assert marker.read_text(encoding='utf-8')=='synthetic profile sentinel'
  instance=json.loads((root/'running-instance.json').read_text(encoding='utf-8'))
  assert instance['pid']==child.pid and instance['url']==base
  # Reject credentials or an unexpected destination in generated process args.
  args=children[0].args
  assert ('--stable' in args)==(ENVIRONMENT=='production')
  print(json.dumps({'realReplacement':True,'environment':ENVIRONMENT,'channel':CHANNEL,'samePort':True,'sameIsolatedProfile':True}),flush=True)
 finally:
  if thread.is_alive():http.shutdown()
  http.server_close()
  for child in children:
   if child.poll() is None:child.terminate();child.wait(timeout=10)

if '--scenario' in sys.argv:
 scenario()
else:
 for stable in (True,False):
  with tempfile.TemporaryDirectory(prefix='cad3-real-restart-',ignore_cleanup_errors=True) as folder:
   env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':folder}
   for key in ('CAD_COMMUNITY_MODE','CAD_COMMUNITY_URL','CAD_COMMUNITY_TESTING','CAD_DEVELOPMENT'):env.pop(key,None)
   # Signed out, empty profile; settings disable unattended work and update checks.
   Path(folder,'settings.json').write_text(json.dumps({'autoCapture':False,'checkForUpdates':False}))
   args=[sys.executable,str(Path(__file__).resolve()),'--scenario']+(['--stable'] if stable else [])
   result=subprocess.run(args,cwd=test_safety.ROOT,env=env,text=True,capture_output=True,timeout=45)
   assert result.returncode==0,result.stdout+result.stderr
   print(result.stdout,flush=True)
 # Exercise the packaged command branch without launching a real installed app.
 import server
 http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
 thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
 try:
  with patch.object(server,'RUNNING_PORT',http.server_port), patch.object(server,'ENVIRONMENT','production'), \
       patch.object(server.UPDATES,'supported',True), patch.object(server.subprocess,'Popen') as spawn:
   request=urllib.request.Request(f'http://127.0.0.1:{http.server_port}/api/app/restart',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
   with urllib.request.urlopen(request,timeout=5) as response:assert json.load(response)['restarting']
   args=spawn.call_args.args[0]
   assert args==[str(server.UPDATES.executable),'--port',str(http.server_port),'--no-browser','--replacing']
 finally:
  http.shutdown();http.server_close();thread.join(timeout=5)
 print('PASS: real stable/development source replacements keep environment, channel, port and isolated profile')

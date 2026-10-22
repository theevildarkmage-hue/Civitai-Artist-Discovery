"""Single optional registration point around the normal development server."""
import sys
if getattr(sys,'frozen',False):raise RuntimeError('Test Lab cannot be loaded by a packaged application')
import json,os,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
if os.environ.get('CAD_DEVELOPMENT')!='1':raise RuntimeError('Explicit development mode required')
assert Path(sys.argv[1]).resolve()==ROOT/'server.py'
connection=Path(os.environ['CAD_TEST_LAB_CONNECTION']).resolve()
assert ROOT not in connection.parents
state=json.loads(connection.read_text());url=urlsplit(state['manager'])
assert url.scheme=='http' and url.hostname=='127.0.0.1' and url.port
sys.path.insert(0,str(ROOT));import server
def proxy(path,body=None):
    req=urllib.request.Request(state['manager']+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-Lab-Capability':state['capability']})
    with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)
old_get=server.Handler.do_GET;old_post=server.Handler.do_POST
def get(handler):
    if not handler.host_allowed():handler.send_error(403);return
    path=urlsplit(handler.path).path
    if path.startswith('/__dev/community-test-lab/'):
        try:
            handler.require_community_session()
            if path.endswith('/registration'):handler.json_response({'connectionFile':str(connection)});return
            if path.endswith('/entry.js'):
                raw=(HERE/'entry.js').read_bytes();handler.send_response(200);handler.send_header('Content-Type','text/javascript');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw);return
            if path.endswith('/entry.css'):
                raw=(HERE/'entry.css').read_bytes();handler.send_response(200);handler.send_header('Content-Type','text/css');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw);return
            if path.endswith('/status'):handler.json_response(proxy('/status'));return
        except Exception:handler.send_error(503,'Local Community Test Lab unavailable');return
    if path=='/ui/development.js':
        raw=(server.STATIC/'ui/development.js').read_bytes()+b'\nimport "/__dev/community-test-lab/entry.js";\n'
        handler.send_response(200);handler.send_header('Content-Type','text/javascript');handler.send_header('X-Content-Type-Options','nosniff');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw);return
    old_get(handler)
def post(handler):
    if not handler.host_allowed():handler.send_error(403);return
    path=urlsplit(handler.path).path
    if path=='/api/app/restart':
        import subprocess
        try:
            handler.request_json();handler.require_community_session()
            subprocess.Popen([sys.executable,'-B',str(HERE/'launch.py'),str(ROOT/'server.py'),'--port',str(server.RUNNING_PORT),'--no-browser','--replacing'],
                cwd=ROOT,env=os.environ.copy(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            handler.json_response({'restarting':True});server.request_app_shutdown(handler.server);return
        except Exception:handler.send_error(400,'Development restart failed');return
    if path in ('/__dev/community-test-lab/open','/__dev/community-test-lab/inspect','/__dev/community-test-lab/shutdown'):
        try:
            handler.require_community_session();body=handler.request_json()
            handler.json_response(proxy('/'+path.rsplit('/',1)[-1],body));return
        except Exception:handler.send_error(400,'Invalid local lab request');return
    old_post(handler)
server.Handler.do_GET=get;server.Handler.do_POST=post
sys.argv=[str(ROOT/'server.py'),*sys.argv[2:]]
server.main()

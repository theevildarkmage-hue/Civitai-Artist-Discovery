"""Real loopback HTTP with simulated private peers; no external listener or OAuth."""
import test_safety
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix='cad3-lan-security-') as temporary:
    os.environ['CIVITAI_HISTORY_DATA_DIR']=temporary
    import server
    from discovery.lan import LanAccess, private_address
    server.SETTINGS.update(allow_lan_access_value=True,auto_capture_value=False)
    server.LAN.addresses=['192.168.10.2']

    class PeerHandler(server.Handler):
        def setup(self):
            super().setup()
            if self.server.remote:self.client_address=('192.168.10.3',self.client_address[1])
        def log_message(self,*args):pass

    listener=server.ThreadingHTTPServer(('127.0.0.1',0),PeerHandler)
    port=listener.server_address[1]
    # Simulate the listener metadata only. The actual socket stays on loopback.
    listener.server_address=('0.0.0.0',port);listener.remote=True
    server.RUNNING_PORT=port
    thread=threading.Thread(target=listener.serve_forever,daemon=True);thread.start()
    host=f'192.168.10.2:{port}'
    origin='http://'+host
    def request(path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        supplied={'Host':host,**(headers or {})}
        if body is not None:supplied.update({'Content-Type':'application/json'})
        connection.request('POST' if body is not None else 'GET',path,
                           body=None if body is None else json.dumps(body),headers=supplied)
        response=connection.getresponse();data=response.read();cookie=response.getheader('Set-Cookie')
        connection.close();return response.status,data,cookie
    try:
        assert request('/')[0]==200 and b'Connect to Discovery' in request('/')[1]
        assert request('/lan-login.js')[0]==200
        for path in ('/api/auth-status','/api/settings','/api/history/config','/app.js','/api/community/beta'):
            assert request(path)[0]==403,path
        assert request('/api/settings',headers={'Host':f'127.0.0.1:{port}'})[0]==403
        assert request('/api/settings',headers={'Host':'attacker.example'})[0]==403
        assert request('/api/lan/pair',{'key':'bad'},headers={'Origin':origin})[0]==403
        assert request('/api/lan/pair',{'key':'x'*43})[0]==403
        assert request('/api/lan/pair',{'key':'x'*43},headers={'Origin':'http://evil.example'})[0]==403
        # Obtain the capability through the real local-only route, not a public API.
        listener.remote=False
        status=json.loads(request('/api/lan/status',headers={'Host':f'127.0.0.1:{port}'})[1])
        key=status['links'][0].split('#pair=',1)[1]
        assert len(key)>=40
        listener.remote=True
        code,_,cookie=request('/api/lan/pair',{'key':key},headers={'Origin':origin})
        assert code==200 and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
        authenticated={'Cookie':cookie.split(';',1)[0],'Origin':origin}
        assert request('/api/settings',headers=authenticated)[0]==200
        for path in ('/api/lan/status','/api/oauth/client','/api/development/diagnostics','/api/update/status'):
            assert request(path,headers=authenticated)[0]==403,path
        for path in ('/api/app/close','/api/discovery/reset','/api/oauth/disconnect','/api/update/install'):
            assert request(path,{},headers=authenticated)[0]==403,path
        assert request('/api/settings',{'allowLanAccess':False},headers=authenticated)[0]==403
        assert request('/api/settings',{'dimSeenCards':False},headers={'Cookie':authenticated['Cookie']})[0]==403
        assert request('/api/settings',{'dimSeenCards':False},headers={**authenticated,'Origin':'http://evil.example'})[0]==403
        assert request('/api/settings',{'dimSeenCards':False},headers=authenticated)[0]==200
        assert request('/api/community/recommend',{'artwork':123},headers=authenticated)[0] in (401,403)
        http_remote_cookie=authenticated.copy()
        listener.remote=False
        assert request('/api/settings',{'allowLanAccess':False},headers={'Host':f'127.0.0.1:{port}'})[0]==200
        assert request('/api/settings',{'allowLanAccess':True},headers={'Host':f'127.0.0.1:{port}'})[0]==200
        listener.remote=True
        assert request('/api/settings',headers=http_remote_cookie)[0]==403,'Disable/re-enable must revoke old cookie'
        server.LAN=LanAccess();server.LAN.addresses=['192.168.10.2']
        assert request('/api/settings',headers=authenticated)[0]==403,'Restart must revoke pairing'
        listener.remote=False
        with patch.object(server.subprocess,'Popen') as launch,patch.object(server,'request_app_shutdown') as shutdown:
            assert request('/api/app/restart',{},headers={'Host':f'127.0.0.1:{port}'})[0]==200
            launch.assert_called_once();shutdown.assert_called_once_with(listener)
        listener.remote=True
        server.SETTINGS.update(allow_lan_access_value=False)
        assert request('/')[0]==403,'Disable must block immediately'
        assert all(not private_address(ip) for ip in ('8.8.8.8','127.0.0.1','169.254.1.2','::1','100.64.0.1'))
        assert not server.LAN.accept('\u2603'*43)
    finally:listener.shutdown();listener.server_close();thread.join(timeout=5)
print('PASS: private-peer HTTP pairing, anonymous denials, Host/Origin/CSRF guards, local-only administration, OAuth required, restart revocation and immediate disable; peer identity simulated, no real LAN test')

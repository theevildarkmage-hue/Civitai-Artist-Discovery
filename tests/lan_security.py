"""2.2.1 direct-IP LAN behavior with Host/Origin and Civitai boundaries intact."""
import test_safety
import http.client,json,os,tempfile,threading
from unittest.mock import patch,Mock
with tempfile.TemporaryDirectory(prefix='cad3-direct-lan-') as temporary:
 os.environ['CIVITAI_HISTORY_DATA_DIR']=temporary
 import server
 server.SETTINGS.update(allow_lan_access_value=True,auto_capture_value=False)
 class PeerHandler(server.Handler):
  def setup(self):
   super().setup();self.client_address=('192.168.10.3',self.client_address[1])
  def log_message(self,*args):pass
 listener=server.ThreadingHTTPServer(('127.0.0.1',0),PeerHandler);port=listener.server_address[1]
 worker=threading.Thread(target=listener.serve_forever,daemon=True);worker.start()
 host=f'192.168.10.2:{port}'
 def request(path,body=None,headers=None):
  connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
  supplied={'Host':host,**(headers or {})}
  if body is not None:supplied['Content-Type']='application/json'
  connection.request('POST' if body is not None else 'GET',path,None if body is None else json.dumps(body),supplied)
  response=connection.getresponse();data=response.read();cookie=response.getheader('Set-Cookie');connection.close()
  return response.status,data,cookie
 try:
  with patch.object(server,'lan_addresses',return_value=['192.168.10.2']):
   assert request('/')[0]==200
   assert b'pair=' not in request('/')[1]
   assert request('/api/settings')[0]==200
   assert request('/api/settings')[2] is None
   assert request('/api/settings',headers={'Host':'attacker.example'})[0]==403
   assert request('/api/settings',{'dimSeenCards':False},headers={'Origin':'http://evil.example'})[0]==403
   assert request('/api/settings',{'dimSeenCards':False},headers={'Origin':'http://'+host})[0]==200
   assert request('/api/community/recommend',{'artwork':123},headers={'Origin':'http://'+host})[0] in (401,403)
   # A genuine, already-verified host session may use Community over LAN;
   # simulator identities remain loopback-only even if LAN is enabled.
   fake=Mock();fake.request.return_value={'allowed':True};fake.mode='supabase'
   with patch.object(server.Handler,'require_community_session'),patch.object(server,'community_client',return_value=fake),patch('discovery.community_beta.require_enabled'):
    assert request('/api/community/status')[0]==200
    fake.request.assert_called_once()
    fake.request.reset_mock()
    with patch('discovery.community_security.test_identity',return_value='isolated-fixture'):
     assert request('/api/community/status')[0]==403
     fake.request.assert_not_called()
   assert request('/api/lan/pair',{'key':'obsolete'},headers={'Origin':'http://'+host})[0] != 200
   assert request('/api/lan/status')[0]==404
   assert server.is_own_app_url('http://'+host)
   server.SETTINGS.update(allow_lan_access_value=False)
   assert request('/')[0]==403
 finally:listener.shutdown();listener.server_close();worker.join(timeout=5)
print('PASS: direct LAN HTTP without cookies/pairing, own-address Host/Origin checks, disabled rejection, Community OAuth required; private peer simulated')

"""Dedicated Supabase Auth + TOTP operator. No application/management secrets.

Enroll the Auth identity and private UUID mapping separately under owner approval.
The public key is not authority. Password and one-time code are entered privately.
"""
import argparse,base64,getpass,html,json,os,re,secrets,sys,threading,urllib.request,urllib.error,webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
BASE='https://aibelmhucizupzvvhdvf.supabase.co'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['enroll','configure','invite','revoke_invite','invitation_status','block','unblock','hide','remove','resolve_report','dismiss_report','reports','audit','registry_stats','registry_delete','registry_retention'])
    p.add_argument('--target',type=int);p.add_argument('--reason');p.add_argument('--until')
    p.add_argument('--profile',help='Owner-approved Civitai username or profile URL; required for invitation enrollment')
    a=p.parse_args();key=os.environ.get('SUPABASE_PUBLISHABLE_KEY','')
    if not sys.stdin.isatty():p.error('Use a private interactive terminal; never pipe passwords or MFA codes')
    if not re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]+',key):p.error('Modern public client key required; privileged keys prohibited')
    if a.action=='invite' and (not a.profile or a.target is not None):p.error('Invitation requires an owner-approved profile, not an unverified raw ID')
    if a.profile:
        from discovery.invitations import resolve_account
        try:a.target=resolve_account(a.profile)['id']
        except Exception:p.error('Public profile identity could not be verified; no invitation attempted')
    access=None;created_factor=None;qr_server=None
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(NoRedirect)
    def request(path,body=None,method=None):
        assert path.startswith(('/auth/v1/','/rest/v1/rpc/community_admin'))
        req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
          method=method,headers={'apikey':key,'Content-Type':'application/json',**({'Authorization':'Bearer '+access} if access else {})})
        try:
            with opener.open(req,timeout=20) as r:
                raw=r.read(1048577)
                if len(raw)>1048576:raise RuntimeError('Oversized response')
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            code=e.code;e.close();raise RuntimeError(f'Operator request denied/unavailable (HTTP {code}); response withheld') from None
    try:
        email=input('Dedicated staging operator email: ').strip()
        session=request('/auth/v1/token?grant_type=password',{'email':email,'password':getpass.getpass('Operator password: ')})
        access=session['access_token'];user=request('/auth/v1/user')
        factors=[f for f in user.get('factors',[]) if f.get('factor_type')=='totp' and f.get('status')=='verified']
        if a.action=='enroll':
            if user.get('factors'):raise RuntimeError('Existing factors found; use approved recovery rather than modifying them')
            factor_data=request('/auth/v1/factors',{'factor_type':'totp','friendly_name':'Discovery staging operator'})
            created_factor=factor_data['id']
            if not re.fullmatch('[0-9a-f-]{36}',created_factor):raise RuntimeError('Unexpected factor identity')
            svg=factor_data.get('totp',{}).get('qr_code','')
            if not isinstance(svg,str) or len(svg)>1048576 or '<svg' not in svg:raise RuntimeError('Unexpected enrollment QR format')
            image='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()
            nonce='/'+secrets.token_urlsafe(32)
            page=('<!doctype html><title>Discovery staging operator MFA</title><h1>Scan with your authenticator</h1><p>Return to the private terminal to enter the code. Do not save or share this page.</p><img alt="Private MFA enrollment QR" src="'+html.escape(image,quote=True)+'">').encode()
            class QRPage(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    if self.path!=nonce:self.send_error(404);return
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'none'; img-src data:; frame-ancestors 'none'; base-uri 'none'");self.send_header('Content-Length',str(len(page)));self.end_headers();self.wfile.write(page)
            qr_server=ThreadingHTTPServer(('127.0.0.1',0),QRPage)
            threading.Thread(target=qr_server.serve_forever,daemon=True).start()
            webbrowser.open('http://127.0.0.1:'+str(qr_server.server_port)+nonce)
            factors=[{'id':created_factor}]
        if len(factors)!=1:raise RuntimeError('Exactly one verified TOTP factor required; complete approved enrollment first')
        factor=factors[0]['id']
        if not re.fullmatch('[0-9a-f-]{36}',factor):raise RuntimeError('Unexpected factor identity')
        challenge=request('/auth/v1/factors/'+factor+'/challenge',{})
        code=getpass.getpass('Current authenticator code: ')
        if not re.fullmatch(r'\d{6}',code):raise RuntimeError('Six-digit TOTP required')
        access=request('/auth/v1/factors/'+factor+'/verify',{'challenge_id':challenge['id'],'code':code})['access_token']
        if a.action=='enroll':
            created_factor=None
            print(json.dumps({'enrolled':True,'authUserId':user['id'],'operatorMappingCreated':False}));return 0
        result=request('/rest/v1/rpc/community_admin',{'p_action':a.action,'p_target':a.target,'p_reason':a.reason,'p_until':a.until})
        print(json.dumps(result));return 0
    except Exception as e:
        # Only our bounded constant diagnostics, never network body/credentials.
        print(str(e) if isinstance(e,RuntimeError) else 'Operator operation stopped; details withheld',file=sys.stderr);return 1
    finally:
        if qr_server:qr_server.shutdown();qr_server.server_close()
        if created_factor and re.fullmatch('[0-9a-f-]{36}',created_factor):
            try:request('/auth/v1/factors/'+created_factor,method='DELETE')
            except Exception:print('Unverified factor cleanup could not be confirmed; inspect that exact account',file=sys.stderr)
        if access:
            try:request('/auth/v1/logout?scope=local',{})
            except Exception:print('Session sign-out could not be confirmed; use the incident runbook',file=sys.stderr)

if __name__=='__main__':raise SystemExit(main())

"""Consent-gated, best-effort authenticated activity; no durable retry queue."""
import json
from pathlib import Path
import platform
import threading
import time

DISCLOSURE='2026-10-23'
# Activation requires the privacy decision and a compatible hosted deployment.
COLLECTION_ENABLED=False
ENDPOINT='https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1'


class RegistryClient:
    def __init__(self,data,version,*,enabled=COLLECTION_ENABLED,clock=time.monotonic,send=None):
        self.path=Path(data)/'registry-consent.json';self.version=version;self.enabled=enabled
        self.clock=clock;self.send=send;self.lock=threading.Lock();self.due={};self.active=False

    def consent(self):
        try:value=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):value={}
        return {'consented':value.get('disclosure')==DISCLOSURE and value.get('consented') is True,
                'disclosure':DISCLOSURE,'collectionEnabled':self.enabled}

    def set_consent(self,value):
        if type(value) is not bool:raise ValueError('Choose true or false')
        with self.lock:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'consented':value,'disclosure':DISCLOSURE}))
            temp.replace(self.path)
        return self.consent()

    def authenticated(self,identity,token_provider):
        # A background launch alone never enters this path. This ID only scopes
        # the local retry budget; the hosted handler independently verifies it.
        if not self.enabled or not self.consent()['consented']:return False
        key=identity.get('id')
        if not isinstance(key,int) or isinstance(key,bool) or key<=0:return False
        with self.lock:
            now=self.clock()
            if self.active or self.due.get(key,0)>now:return False
            self.active=True
            self.due={k:v for k,v in self.due.items() if v>now}
            if len(self.due)>=64:self.active=False;return False
            self.due[key]=now+7200
        def run():
            try:
                from .community import bounded_json
                token=token_provider()
                with self.lock:
                    if not self.consent()['consented']:return
                result=(self.send or bounded_json)(ENDPOINT,token,{'apiVersion':1,'operation':'register',
                    'appVersion':self.version,'platform':{'Windows':'Windows','Linux':'Linux','Darwin':'macOS'}.get(platform.system(),'Other'),
                    'disclosure':DISCLOSURE})
                if result.get('apiVersion')==1 and result.get('registered') is True:
                    with self.lock:self.due[key]=self.clock()+43200
            except Exception:pass  # Never log tokens or block the personal gallery.
            finally:
                with self.lock:self.active=False
        threading.Thread(target=run,name='registry-heartbeat',daemon=True).start()
        return True

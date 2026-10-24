"""Automatic, best-effort authenticated activity; no local state or retry queue."""
import platform
import logging
import threading
import time

ENDPOINT='https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1'


class RegistryClient:
    def __init__(self,version,*,clock=time.monotonic,send=None):
        self.version=version
        self.clock=clock;self.send=send;self.lock=threading.Lock();self.due={};self.active=False

    def reauthenticated(self,identity):
        # A newly obtained restricted grant can repair an earlier auth failure.
        # SQL still enforces the12h write interval; this only releases local retry delay.
        with self.lock:self.due.pop(identity.get('id'),None)

    def authenticated(self,identity,token_provider):
        # A background launch alone never enters this path. This ID only scopes
        # the local retry budget; the hosted handler independently verifies it.
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
                from .cloud_identity import SESSION
                if not isinstance(token,str) or not SESSION.fullmatch(token):return
                result=(self.send or bounded_json)(ENDPOINT,token,{'apiVersion':1,'operation':'register',
                    'appVersion':self.version,'platform':{'Windows':'Windows','Linux':'Linux','Darwin':'macOS'}.get(platform.system(),'Other')})
                # A disabled hosted registry also asks clients to wait twelve hours.
                if result.get('apiVersion')==1 and (result.get('registered') is True or result.get('retryAfterSeconds')==43200):
                    with self.lock:self.due[key]=self.clock()+43200
            except Exception as error:
                # Log only a fixed category, never exception text, request bodies,
                # identities, or credentials. Failure remains nonblocking.
                from .community import CommunityError, MESSAGES
                code=error.code if isinstance(error,CommunityError) and error.code in MESSAGES else 'INTERNAL_OR_TRANSPORT'
                logging.getLogger(__name__).warning('Registry heartbeat deferred: %s',code)
            finally:
                with self.lock:self.active=False
        try:threading.Thread(target=run,name='registry-heartbeat',daemon=True).start()
        except Exception:
            with self.lock:self.active=False
            return False
        return True

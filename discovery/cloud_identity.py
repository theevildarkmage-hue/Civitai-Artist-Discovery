"""Identity-only cloud exchange. This module never accepts an API access token."""
import re
import time
import secrets

ENDPOINT = 'https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1'
SESSION = re.compile(r'sdc_[A-Za-z0-9_-]{43}')

class EnrollmentError(RuntimeError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__('Community authorization needs attention.')


def challenge(audience, *, send=None, clock=time.time):
    from .community import bounded_json
    from .device_identity import generate
    device=generate()
    value = (send or bounded_json)(ENDPOINT, None, {'apiVersion': 2, 'operation': 'identityChallenge','devicePublicKey':device['public']})
    if isinstance(value,dict) and value.get('audience') and value['audience']!=audience:
        raise EnrollmentError('unsupported_client')
    if isinstance(value,dict) and type(value.get('expiresAt')) is int and not clock()<value['expiresAt']<=clock()+330:
        raise EnrollmentError('clock_or_expired_challenge')
    if (not isinstance(value, dict) or value.get('apiVersion') != 2
        or value.get('audience') != audience
        or value.get('devicePublicKey')!=device['public']
        or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value.get('nonce', ''))
        or not isinstance(value.get('challenge'), str) or len(value['challenge']) > 1024
        or type(value.get('expiresAt')) is not int or not clock() < value['expiresAt'] <= clock()+330):
        raise RuntimeError('Cloud identity challenge unavailable')
    return {**value,'_device':device}


def exchange(assertion, pending, expected_id, *, send=None, clock=time.time):
    from .community import bounded_json
    if not isinstance(assertion,str):raise EnrollmentError('missing_identity_proof')
    if pending['expiresAt']<=clock():raise EnrollmentError('clock_or_expired_challenge')
    if (not isinstance(assertion, str) or len(assertion)>8192
        or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', assertion)
        or pending['expiresAt'] <= clock()):
        raise RuntimeError('Cloud identity proof unavailable')
    # An explicit allowlist, not a token-response dictionary, forms the wire body.
    extra={}
    if '_device' in pending:
        from .device_identity import sign,digest
        extra={'deviceSignature':sign(pending['_device'],'discovery-enroll-v1\n'+pending['challenge']+'\n'+digest(assertion))}
    value=(send or bounded_json)(ENDPOINT, None, {'apiVersion':2,'operation':'identityExchange',
        'challenge':pending['challenge'],'identityAssertion':assertion,**extra})
    if (not isinstance(value, dict) or value.get('apiVersion')!=2
        or not isinstance(value.get('session'),str) or not SESSION.fullmatch(value['session'])
        or type(value.get('expiresAt')) is not int or not clock()<value['expiresAt']<=clock()+43260
        or not isinstance(value.get('member'),dict) or type(value['member'].get('id')) is not int
        or value['member']['id']!=expected_id):
        raise RuntimeError('Cloud identity session rejected')
    result={'session':value['session'],'expires_at':value['expiresAt'],'member_id':expected_id}
    if '_device' in pending:result['renewal']=_renewal(value.get('renewal'),pending['_device'],clock)
    return result


def _renewal(value,device,clock):
    if (not isinstance(value,dict) or not isinstance(value.get('credential'),str)
        or not re.fullmatch(r'sdr_[A-Za-z0-9_-]{43}',value['credential'])
        or type(value.get('expiresAt')) is not int or type(value.get('absoluteExpiresAt')) is not int
        or not clock()<value['expiresAt']<=min(value['absoluteExpiresAt'],clock()+30*86400+60)
        or not clock()<value['absoluteExpiresAt']<=clock()+90*86400+60):
        raise RuntimeError('Discovery renewal credential rejected')
    return {'credential':value['credential'],'device':device,'expires_at':value['expiresAt'],'absolute_expires_at':value['absoluteExpiresAt']}


def renew(session,*,send=None,clock=time.time):
    from .community import bounded_json
    from .device_identity import sign,digest
    renewal=session['renewal']
    # Revalidate persisted inputs before any transport, not only at enrollment.
    # An old/malformed profile must never route a Civitai credential into a body.
    if (not isinstance(renewal,dict) or not isinstance(renewal.get('credential'),str)
        or not re.fullmatch(r'sdr_[A-Za-z0-9_-]{43}',renewal['credential'])
        or not isinstance(renewal.get('pending_request'),str)
        or not re.fullmatch(r'[A-Za-z0-9_-]{43}',renewal['pending_request'])):
        raise RuntimeError('Discovery renewal input rejected')
    if min(renewal['expires_at'],renewal['absolute_expires_at'])<=clock():
        raise RuntimeError('Discovery device authorization expired')
    request_id=renewal['pending_request']
    nonce=secrets.token_urlsafe(32);issued=int(clock())
    message='discovery-renew-v1\n'+digest(renewal['credential'])+'\n'+request_id+'\n'+nonce+'\n'+str(issued)
    value=(send or bounded_json)(ENDPOINT,None,{'apiVersion':2,'operation':'identityRenew','credential':renewal['credential'],
        'requestId':request_id,'attemptNonce':nonce,'issuedAt':issued,'signature':sign(renewal['device'],message)})
    if (not isinstance(value,dict) or value.get('apiVersion')!=2 or not isinstance(value.get('session'),str)
        or not SESSION.fullmatch(value['session']) or type(value.get('expiresAt')) is not int
        or not clock()<value['expiresAt']<=clock()+43260 or type(value.get('member',{}).get('id')) is not int
        or value.get('member',{}).get('id')!=session['member_id']):
        raise RuntimeError('Discovery renewed session rejected')
    return {'session':value['session'],'expires_at':value['expiresAt'],'member_id':session['member_id'],
        'renewal':_renewal(value.get('renewal'),renewal['device'],clock)}

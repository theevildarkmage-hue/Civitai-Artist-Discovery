"""Identity-only cloud exchange. This module never accepts an API access token."""
import re
import time

ENDPOINT = 'https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1'
SESSION = re.compile(r'sdc_[A-Za-z0-9_-]{43}')


def challenge(audience, *, send=None, clock=time.time):
    from .community import bounded_json
    value = (send or bounded_json)(ENDPOINT, None, {'apiVersion': 2, 'operation': 'identityChallenge'})
    if (not isinstance(value, dict) or value.get('apiVersion') != 2
        or value.get('audience') != audience
        or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value.get('nonce', ''))
        or not isinstance(value.get('challenge'), str) or len(value['challenge']) > 1024
        or type(value.get('expiresAt')) is not int or not clock() < value['expiresAt'] <= clock()+330):
        raise RuntimeError('Cloud identity challenge unavailable')
    return value


def exchange(assertion, pending, expected_id, *, send=None, clock=time.time):
    from .community import bounded_json
    if (not isinstance(assertion, str) or len(assertion)>8192
        or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', assertion)
        or pending['expiresAt'] <= clock()):
        raise RuntimeError('Cloud identity proof unavailable')
    # An explicit allowlist, not a token-response dictionary, forms the wire body.
    value=(send or bounded_json)(ENDPOINT, None, {'apiVersion':2,'operation':'identityExchange',
        'challenge':pending['challenge'],'identityAssertion':assertion})
    if (not isinstance(value, dict) or value.get('apiVersion')!=2
        or not isinstance(value.get('session'),str) or not SESSION.fullmatch(value['session'])
        or type(value.get('expiresAt')) is not int or not clock()<value['expiresAt']<=clock()+43260
        or not isinstance(value.get('member'),dict) or type(value['member'].get('id')) is not int
        or value['member']['id']!=expected_id):
        raise RuntimeError('Cloud identity session rejected')
    return {'session':value['session'],'expires_at':value['expiresAt'],'member_id':expected_id}

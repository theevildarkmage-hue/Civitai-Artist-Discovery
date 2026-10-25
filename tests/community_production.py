"""Explicit isolated Edge/Discovery-session contract smoke test; never spends recommendation quota."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.community import CommunityError, SupabaseCommunityBackend

def run():
    if os.environ.get('CAD_ALLOW_ISOLATED_EDGE_TESTS')!='1':
        print('SKIP: isolated Edge Function and Discovery-only test session required');return
    backend=SupabaseCommunityBackend(os.environ.get('CAD_TEST_COMMUNITY_URL'),identity_token_provider=lambda:os.environ['CAD_TEST_DISCOVERY_SESSION'])
    token=os.environ.get('CAD_TEST_DISCOVERY_SESSION')
    if not token:raise RuntimeError('Provide a Discovery-only isolated test session through the environment')
    status=backend.request('status',token,{})
    assert status['apiVersion']==1 and status['member']['id']>0 and not status['development']
    quota=status.get('quota')
    if quota is not None:assert quota['limit']==3
    policy=status.get('beta') or {}
    # Disabled invitation-mode status validates identity without invoking quota
    # or invitation RPCs. Policy denial precedes artwork validation by design.
    expected='BETA_DISABLED' if policy.get('available') is False else 'READ_ONLY' if policy.get('submissions') is False else 'INVALID_ARTWORK'
    for artwork in ('https://127.0.0.1/images/1','https://evil.test/images/1'):
        try:backend.request('verify',token,{'artwork':artwork});raise AssertionError('Invalid artwork accepted')
        except CommunityError as error:assert error.code==expected
    backend.identity_token_provider=lambda:'synthetic-invalid-token'
    try:backend.request('status',None,{});raise AssertionError('Invalid token accepted')
    except CommunityError as error:assert error.code=='AUTH_EXPIRED'
    backend.identity_token_provider=lambda:token
    after=backend.request('status',None,{})
    if quota is not None:assert after['quota']['used']==quota['used']
    else:assert after.get('quota') is None
    print('PASS: verified Edge identity and policy errors; no valid write inputs. Disabled quota preservation requires the separate SQL snapshot check; tokens never printed')

if __name__=='__main__':run()

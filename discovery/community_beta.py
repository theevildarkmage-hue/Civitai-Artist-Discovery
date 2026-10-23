"""Local opt-in and a fixed, reviewed beta service. No remotely executable config."""
import json
import os
from pathlib import Path
import sys
import threading

# Reviewed staging connection for owner acceptance; hosted submissions stay disabled.
BETA_ENDPOINT = "https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1"
_lock=threading.Lock()

def production():
    return bool(getattr(sys,'frozen',False)) or os.environ.get('CAD_DEVELOPMENT')!='1'

def configuration(data):
    try:
        value=json.loads((Path(data)/'community-beta.json').read_text())
        opted=value.get('optedIn') is True
    except (OSError,ValueError,AttributeError):opted=False
    return {'production':production(),'serverPolicy':production() or os.environ.get('CAD_COMMUNITY_MODE')=='supabase',
            'available':bool(BETA_ENDPOINT),'optedIn':opted,
            'enabled':not production() or bool(BETA_ENDPOINT and opted)}

def opt_in(data,value):
    if type(value) is not bool:raise ValueError('Choose true or false')
    if value and not BETA_ENDPOINT:raise ValueError('Community Beta rollout is not available yet')
    path=Path(data)/'community-beta.json'
    with _lock:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps({'optedIn':value}))
        temporary.replace(path)
    return configuration(data)

def require_enabled(data):
    if not configuration(data)['enabled']:
        from .community import CommunityError
        raise CommunityError('FORBIDDEN',403)

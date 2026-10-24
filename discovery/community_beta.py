"""Automatic connection to the reviewed service; hosted invitations control access."""
import os
import sys

# Fixed reviewed service. Identity, invitations and submission policy are server-side.
BETA_ENDPOINT = "https://aibelmhucizupzvvhdvf.supabase.co/functions/v1/community-v1"


def production():
    return bool(getattr(sys,'frozen',False)) or os.environ.get('CAD_DEVELOPMENT')!='1'


def configuration(data):
    # Legacy community-beta.json preferences are ignored and never rewritten.
    return {'production':production(),'serverPolicy':production() or os.environ.get('CAD_COMMUNITY_MODE')=='supabase',
            'available':bool(BETA_ENDPOINT),'enabled':not production() or bool(BETA_ENDPOINT)}


def require_enabled(data):
    if not configuration(data)['enabled']:
        from .community import CommunityError
        raise CommunityError('UNAVAILABLE',503)

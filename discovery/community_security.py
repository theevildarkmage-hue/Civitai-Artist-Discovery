"""Production-safe boundary; development implementations are excluded from builds."""
import sys

def release_boundary():
    from .build_config import ENVIRONMENT
    return bool(getattr(sys,'frozen',False)) or ENVIRONMENT == 'production'

def fixture_enabled():
    if release_boundary():return False
    from .community_dev import fixture_enabled as implementation
    return implementation()

def test_identity(data_root):
    if release_boundary():return None
    from .community_dev import test_identity as implementation
    return implementation(data_root)

def authorize_test(headers,data_root):
    if release_boundary():return None
    from .community_dev import authorize_test as implementation
    return implementation(headers,data_root)

def disable_test_outbound(data_root):
    if release_boundary():return
    from .community_dev import disable_test_outbound as implementation
    implementation(data_root)

def control(client,body):
    if release_boundary():raise PermissionError('Development controls are excluded')
    from .community_dev import control as implementation
    return implementation(client,body)

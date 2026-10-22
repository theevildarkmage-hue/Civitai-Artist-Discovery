"""Production-safe boundary; development implementations are excluded from builds."""
import sys

def fixture_enabled():
    if getattr(sys,'frozen',False):return False
    from .community_dev import fixture_enabled as implementation
    return implementation()

def test_identity(data_root):
    if getattr(sys,'frozen',False):return None
    from .community_dev import test_identity as implementation
    return implementation(data_root)

def authorize_test(headers,data_root):
    if getattr(sys,'frozen',False):return None
    from .community_dev import authorize_test as implementation
    return implementation(headers,data_root)

def disable_test_outbound(data_root):
    if getattr(sys,'frozen',False):return
    from .community_dev import disable_test_outbound as implementation
    implementation(data_root)

def control(client,body):
    if getattr(sys,'frozen',False):raise PermissionError('Development controls are excluded')
    from .community_dev import control as implementation
    return implementation(client,body)

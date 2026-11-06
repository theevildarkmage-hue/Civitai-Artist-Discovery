"""Explicit, loopback-only fixture controls. Never part of Supabase identity trust."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from .community import CommunityError, FixtureVerifier, LocalCommunityBackend


def fixture_enabled():
    return not getattr(sys,'frozen',False) and os.environ.get('CAD_DEVELOPMENT') == '1' and os.environ.get('CAD_COMMUNITY_MODE') == 'fixture'


def test_identity(data_root):
    if not fixture_enabled() or os.environ.get('CAD_COMMUNITY_TESTING') != '1':
        return None
    nonce = os.environ.get('CAD_TEST_INSTANCE', '')
    root = Path(data_root).resolve()
    project = Path(__file__).resolve().parents[1]
    pin=project/'.data-directory-3.0.json'
    selected=(project/json.loads(pin.read_text(encoding='utf-8'))['directory']).resolve() if pin.exists() else project/'data-dev-3.0'
    if root in (selected,project/'data-test-community',project/'data-dev-3.0'):
        raise RuntimeError('Interactive profiles cannot be used as automated fixture sandboxes')
    temp_root=Path(tempfile.gettempdir()).resolve()
    if (len(nonce) < 24 or not (project in root.parents or temp_root in root.parents)
            or not root.name.startswith('data-test-')):
        raise RuntimeError('Automated fixture server requires an isolated test data root and instance challenge')
    return {'instance': nonce, 'dataIdentity': hashlib.sha256(str(root).casefold().encode()).hexdigest(), 'fixture': True}


def authorize_test(headers, data_root):
    identity = test_identity(data_root)
    if identity and not secrets.compare_digest(headers.get('X-CAD-Test-Instance', ''), identity['instance']):
        raise PermissionError('Automated tests require the isolated instance challenge')
    return identity


def disable_test_outbound(data_root):
    if test_identity(data_root):
        def refused(*args, **kwargs):
            raise PermissionError('Outbound network is disabled in the isolated community test process')
        socket.create_connection = refused


def control(client, body):
    if not fixture_enabled() or os.environ.get('CAD_COMMUNITY_TESTING') != '1' or not isinstance(client.verifier, FixtureVerifier):
        raise CommunityError('FORBIDDEN', 403)
    verifier = client.verifier
    def set_clock(value):
        client.backend.clock = lambda: value
        path = client.backend.path.parent / 'test-clock.json'
        path.write_text(json.dumps(value.isoformat()), encoding='utf-8')
    def persist_upstream():
        path = client.backend.path.parent / 'fixture-upstream-state.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'hidden':verifier.hidden,'removed':sorted(verifier.removed)}),encoding='utf-8')
        os.replace(temporary,path)
    action = body.get('action')
    if action == 'reset':
        # Test root was independently checked before the route calls this function.
        with client.backend.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM submission_requests')
            db.execute('DELETE FROM audit'); db.execute('DELETE FROM recommendations')
            db.execute("DELETE FROM sqlite_sequence WHERE name='recommendations'")
        verifier.artworks = dict(verifier.initial_artworks)
        verifier.artwork_failures = dict(verifier.initial_failures)
        verifier.hidden.clear(); verifier.removed.clear(); verifier.failure = None; verifier.expire_during_artwork = False
        persist_upstream()
        set_clock(datetime.fromisoformat('2026-10-14T12:00:00+00:00'))
        client.backend.rate.buckets.clear(); client.backend.read_rate.buckets.clear(); client.rate.buckets.clear()
        client.dev_offline = False; client.dev_lost_response = False
    elif action == 'clock':
        value = datetime.fromisoformat(body['now'])
        if value.tzinfo is None: raise CommunityError('INVALID_REQUEST')
        set_clock(value)
    elif action == 'failure':
        value = body.get('code')
        if value not in (None, 'UNAVAILABLE', 'AUTH_EXPIRED', 'RATE_LIMITED'):
            raise CommunityError('INVALID_REQUEST')
        verifier.failure = value
        verifier.expire_during_artwork = bool(body.get('duringArtwork', False))
    elif action == 'hidden':
        member = int(body['member'])
        if f'fixture:{member}' not in verifier.members: raise CommunityError('INVALID_REQUEST')
        from .community import content_profile
        content_profile(body['controls'], (1,2,4,8,16))
        verifier.hidden[f'fixture:{member}'] = body['controls']
        persist_upstream()
    elif action == 'removed':
        image = int(body['image'])
        verifier.artworks.pop(image, None); verifier.removed.add(image); persist_upstream()
    elif action == 'offline':
        client.dev_offline = bool(body.get('enabled'))
    elif action == 'loseConfirmationResponse':
        client.dev_lost_response = True
    elif action == 'snapshot':
        with client.backend.connect() as db:
            return {'recommendations': [dict(row) for row in db.execute('SELECT * FROM recommendations ORDER BY seq')],
                    'audit': [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY seq')]}
    else:
        raise CommunityError('INVALID_REQUEST')
    return {'apiVersion': 1, 'development': True}

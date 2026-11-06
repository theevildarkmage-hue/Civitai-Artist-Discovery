"""Reviewed per-build identity and project selection; never a client endpoint option."""
import json
import re
import sys
from pathlib import Path

DEVELOPMENT_PROJECT = 'aibelmhucizupzvvhdvf'
DEFAULT = {'schemaVersion': 1, 'channel': 'beta', 'version': '3.0.0-beta.14',
           'environment': 'development', 'projectRef': DEVELOPMENT_PROJECT, 'preview': False}
STABLE_SOURCE = {'schemaVersion': 1, 'channel': 'stable', 'version': '3.0.0',
                 'environment': 'production', 'projectRef': 'masrxykkbqngtbeeqpbq', 'preview': False}


def stable_source_requested():
    """Explicit source entry point; never accept arbitrary endpoints/configuration."""
    arguments = sys.argv[1:]
    if '--' in arguments:
        arguments = arguments[:arguments.index('--')]
    return not getattr(sys, 'frozen', False) and '--stable' in arguments


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Invalid build configuration fields')
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or type(value['preview']) is not bool:
        raise ValueError('Invalid build configuration version')
    channel, version, environment, project = (value[k] for k in ('channel', 'version', 'environment', 'projectRef'))
    if channel not in ('beta', 'stable') or environment not in ('development', 'production'):
        raise ValueError('Explicit channel and environment required')
    core = r'3\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)'
    pattern = core + r'-beta\.[1-9]\d*' if channel == 'beta' else core
    if not isinstance(version, str) or not re.fullmatch(pattern, version):
        raise ValueError('Version and channel disagree')
    if not isinstance(project, str) or not re.fullmatch(r'[a-z]{20}', project):
        raise ValueError('Expected a Supabase project reference, never a URL or credential')
    if environment == 'development' and project != DEVELOPMENT_PROJECT:
        raise ValueError('Development project mismatch')
    if environment == 'production' and (channel != 'stable' or project == DEVELOPMENT_PROJECT):
        raise ValueError('Production needs a distinct explicitly selected stable project')
    if channel == 'stable' and environment != 'production' and not value['preview']:
        raise ValueError('Stable Development builds must be marked preview')
    return dict(value)


def load():
    path = Path(__file__).with_name('build-configuration.json')
    if getattr(sys, 'frozen', False):
        if not path.is_file() or path.stat().st_size > 4096:
            raise RuntimeError('Packaged build configuration missing or invalid')
        return validate(json.loads(path.read_text(encoding='utf-8')))
    # Source execution cannot be repointed by an environment variable or profile file.
    # --stable selects only this reviewed public release, before any clients import.
    return validate(STABLE_SOURCE if stable_source_requested() else DEFAULT)


CONFIG = load()
CHANNEL = CONFIG['channel']
VERSION = CONFIG['version']
ENVIRONMENT = CONFIG['environment']
PROJECT_REF = CONFIG['projectRef']
APPLICATION_ID = 'CivitaiDiscoveryBeta' if CHANNEL == 'beta' else 'CivitaiArtistDiscovery'
APP_NAME = 'Civitai Discovery 3.0' + (' Beta' if CHANNEL == 'beta' else '')
if CHANNEL == 'stable' and CONFIG['preview'] and ENVIRONMENT == 'development':
    APP_NAME += ' (Development Preview)'
ENDPOINT = f'https://{PROJECT_REF}.supabase.co/functions/v1/community-v1'

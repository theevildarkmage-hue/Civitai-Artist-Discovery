"""Lexical checks that must run before touching an untrusted filesystem path."""
from urllib.parse import unquote


def static_relative_path(path: str) -> str:
    decoded = unquote(path, errors='strict')
    if decoded == '/':
        return 'index.html'
    if (not decoded.startswith('/') or '\\' in decoded or ':' in decoded
            or any(ord(character) < 32 or ord(character) == 127 for character in decoded)):
        raise ValueError('Invalid static path')
    relative = decoded[1:]
    if any(part in ('', '.', '..') for part in relative.split('/')):
        raise ValueError('Invalid static path')
    return relative


def browser_request_allowed(headers) -> bool:
    # Missing Fetch Metadata remains supported for non-browser local tools.
    # A browser's same-site request can originate from a different local port.
    return headers.get('Sec-Fetch-Site', '').lower() in ('', 'none', 'same-origin')

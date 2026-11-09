"""Policy-free runtime redaction; strict identity scanning belongs to build tools.

Keep this module independent of privacy_patterns and its Unicode/native imports.
Local diagnostics remain private and must pass the strict publication scan before
sharing. No owner-specific identifiers or private policy are loaded by the app.
"""
import re

_HOME = re.compile(r'(?i)(?:[a-z]:[\\/]+users[\\/]+|(?<![A-Za-z0-9:/])/(?:home|users|mnt/[a-z]/users)/)([^\\/\s<>"\x00]+)')
_EMAIL = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')


def redact_personal(message: str) -> str:
    """Preserve the shipped policy-free home/email protections without policy I/O."""
    return _EMAIL.sub('[EMAIL]', _HOME.sub('[LOCAL_HOME]', message))

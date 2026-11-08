"""Personal-identity guard. Protected values are fingerprints, never diagnostic text.

Public creator handles, approved contact channels and ordinary first names are not
blocked. A protected-family-name collision requires review, never attribution edits.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import unicodedata
from urllib.parse import unquote
from pathlib import Path

NAME_HASHES = {}
EMAIL_HASHES = {}
HOME_ACCOUNT_HASHES = {}
_KEY=None
EXPECTED_POLICY_SHA256='e4d726ecc510e4feda426618f06de9cfeb90bb22206d6946e78c9384df928fe2'  # Whole private policy, not a name fingerprint.

def configure_policy(path=None):
    """Load private keyed policy; callers never receive or print its contents."""
    global NAME_HASHES,EMAIL_HASHES,HOME_ACCOUNT_HASHES,_KEY
    raw=Path(path).read_text(encoding='utf-8') if path else os.environ.get('DISCOVERY_IDENTITY_POLICY','')
    if not raw:
        location=os.environ.get('DISCOVERY_IDENTITY_POLICY_FILE','')
        if location:raw=Path(location).read_text(encoding='utf-8')
    if not raw:return False
    value=json.loads(raw)
    pin=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if pin!=EXPECTED_POLICY_SHA256:raise ValueError('Unapproved private policy')
    key=bytes.fromhex(value['key'])
    if len(key)!=32:raise ValueError('Invalid private policy')
    parsed={}
    for name in ('names','emails','homeAccounts'):
        table={}
        for size,digests in value[name].items():
            if not str(size).isdigit() or not 1<=int(size)<=256 or not digests:raise ValueError('Invalid private policy')
            if any(not isinstance(d,str) or not re.fullmatch('[0-9a-f]{64}',d) for d in digests):raise ValueError('Invalid private policy')
            table[int(size)]=frozenset(digests)
        if not table:raise ValueError('Empty private policy')
        parsed[name]=table
    _KEY=key;NAME_HASHES=parsed['names'];EMAIL_HASHES=parsed['emails'];HOME_ACCOUNT_HASHES=parsed['homeAccounts']
    return True

def policy_ready():return _KEY is not None and all((NAME_HASHES,EMAIL_HASHES,HOME_ACCOUNT_HASHES))
def digest(value):
    if _KEY is None:raise ValueError('Private policy unavailable')
    return hmac.new(_KEY,value.encode(),hashlib.sha256).hexdigest()

try:configure_policy()
except Exception:pass  # Runtime keeps generic redaction; security CLIs fail closed.

def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())

def _contains(value, fingerprints):
    for size, digests in fingerprints.items():
        for index in range(len(value)-size+1):
            if digest(value[index:index+size]) in digests:
                return True
    return False

def decoded_views(data):
    if isinstance(data,bytes):
        text=data.decode('utf-8','replace')
        binary=b'\x00' in data or text.count('\ufffd')>len(text)//100
        if binary:
            views={'\n'.join(m[0].decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{4,}',data))}
            views.add('\n'.join(m[0] for m in re.finditer(r'[^\x00-\x1f\x7f\ufffd]{4,}',text) if any(ord(c)>127 for c in m[0])))
            if '\ufffd' not in text:views.add(text)
            if len(data)<=1024*1024:
                views.update(data.decode(enc,'ignore') for enc in ('utf-16-le','utf-16-be'))
            for pattern,encoding in ((rb'(?:[\x20-\x7e]\x00){4,}','utf-16-le'),(rb'(?:\x00[\x20-\x7e]){4,}','utf-16-be')):
                views.add('\n'.join(m[0].decode(encoding) for m in re.finditer(pattern,data)))
        else:views={text}
    else:views={str(data)}
    # Bounded decoding catches common accidental encodings, not arbitrary encryption.
    for _ in range(3):
        additions=set()
        for value in views:
            additions.add(html.unescape(unquote(value)))
            additions.add(re.sub(r'\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})',lambda m:chr(int(m[1] or m[2],16)),value))
            for match in re.finditer(r'(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{16,8192}={0,2}(?![A-Za-z0-9+/_-])',value):
                try:
                    text=base64.urlsafe_b64decode(match[0]+'='*((-len(match[0]))%4)).decode('utf-8')
                    if all(c.isprintable() or c.isspace() for c in text):additions.add(text)
                except (ValueError,UnicodeError):pass
            for match in re.finditer(r'(?<![A-Za-z0-9])[0-9a-fA-F]{16,2048}(?![A-Za-z0-9])',value):
                try:
                    text=bytes.fromhex(match[0]).decode('utf-8')
                    if all(c.isprintable() or c.isspace() for c in text):additions.add(text)
                except (ValueError,UnicodeError):pass
        added=additions-views
        if not added:break
        views.update(added)
    return views

HOME=re.compile(r'(?i)(?:[a-z]:[\\/]+users[\\/]+|(?<![A-Za-z0-9:/])/(?:home|users|mnt/[a-z]/users)/)([^\\/\s<>"\x00]+)')
def categories(data):
    result=set()
    if _KEY and isinstance(data,bytes) and _KEY in data:result.add('private-policy-material')
    for value in decoded_views(data):
        if _KEY:
            markers={_KEY.hex(),base64.b64encode(_KEY).decode(),base64.urlsafe_b64encode(_KEY).decode()}
            markers.update(d for table in (NAME_HASHES,EMAIL_HASHES,HOME_ACCOUNT_HASHES) for digests in table.values() for d in digests)
            if any(marker.casefold() in value.casefold() for marker in markers):result.add('private-policy-material')
        canon=normalized(value)
        if _contains(canon,NAME_HASHES):result.add('protected-name')
        if _contains(canon,EMAIL_HASHES):result.add('protected-email')
        # URL route segments called /users/ are public API identifiers, not homes.
        for match in HOME.finditer(value):
            account=normalized(match[1])
            if _KEY and digest(account) in HOME_ACCOUNT_HASHES.get(len(account),()):result.add('protected-home')
    return result

def redact_personal(message):
    if categories(message):return '[redacted personal-identity detail]'
    # Generic local paths are redacted even when they belong to another machine.
    message=HOME.sub('[LOCAL_HOME]',message)
    return re.sub(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b','[EMAIL]',message)

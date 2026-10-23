"""Fixed-host public Civitai profile resolution; never creates invitations."""
import json,re,urllib.parse
from .community import bounded_json

def resolve_account(reference,fetch=bounded_json):
    name=str(reference).strip()
    if name.startswith('https://'):
        url=urllib.parse.urlsplit(name)
        if url.netloc not in ('civitai.com','www.civitai.com','civitai.red','www.civitai.red') or url.query or url.fragment:
            raise ValueError('Use a public Civitai profile URL')
        match=re.fullmatch('/user/([^/]+)/?',url.path)
        if not match:raise ValueError('Use a Civitai user profile')
        name=urllib.parse.unquote(match[1])
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}',name):raise ValueError('Unsupported profile username; verify manually rather than guessing')
    def query(operation,values):
        raw=fetch('https://civitai.red/api/trpc/'+operation+'?input='+urllib.parse.quote(json.dumps({'json':values},separators=(',',':'))))
        value=raw.get('result',{}).get('data',{})
        if not isinstance(value,dict) or not isinstance(value.get('json'),dict):raise ValueError('Civitai profile resolution unavailable')
        return value['json']
    first=query('user.getCreator',{'username':name})
    identity=first.get('id')
    if type(identity) is not int or identity<=0 or identity>999999999999999 or str(first.get('username','')).casefold()!=name.casefold():raise ValueError('Profile identity mismatch')
    second=query('user.getById',{'id':identity})
    if second.get('id')!=identity or str(second.get('username','')).casefold()!=name.casefold():raise ValueError('Profile changed during resolution; review again')
    return {'id':identity,'username':second['username'],'profileUrl':'https://civitai.red/user/'+urllib.parse.quote(second['username'])}

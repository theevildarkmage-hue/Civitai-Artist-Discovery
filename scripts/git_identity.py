"""Git identity policy. Diagnostics never include names, email values or message text."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery.privacy_patterns import categories as personal_categories
from discovery import privacy_patterns as personal_policy

OWNER_LOGIN='theevildarkmage-hue'
OWNER_EMAIL='260097500+theevildarkmage-hue@users.noreply.github.com'
BLOCKED_EMAIL_DIGESTS=frozenset()
BLOCKED_EMAIL_LENGTHS=frozenset()
# Tests may add synthetic blocked digests in an isolated copy.
OID=re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
EMAIL=re.compile(rb'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+')
IDENT=re.compile(rb'(.+) <([^<>]+)> (-?\d+) ([+-]\d{4})\Z')
ZERO='0'*40
class Rejected(RuntimeError):pass

def blocked(email):
    if isinstance(email,bytes):email=email.decode('utf-8','replace')
    return hashlib.sha256(email.strip().casefold().encode()).hexdigest() in BLOCKED_EMAIL_DIGESTS

def forbidden_text(data):
    if personal_categories(data):return True
    local_chars=b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.!#$%&'*+/=?^_`{|}~-"
    domain_chars=b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-'
    limit=max(BLOCKED_EMAIL_LENGTHS,default=0)
    for match in re.finditer(b'@',data):
        at=match.start();left=at;right=at+1
        while left>max(0,at-limit) and data[left-1] in local_chars:left-=1
        while right<min(len(data),at+limit) and data[right] in domain_chars:right+=1
        token=data[left:right];separator=at-left
        for length in BLOCKED_EMAIL_LENGTHS:
            for begin in range(max(0,separator+2-length),min(separator,len(token)-length)+1):
                if blocked(token[begin:begin+length]):return True
    return False

def git(*args,input=None,check=True):
    process=subprocess.run(['git',*[os.fsdecode(a) if isinstance(a,bytes) else str(a) for a in args]],input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if check and process.returncode:raise Rejected('Git query failed; fetch the required objects and retry (details withheld).')
    return process.stdout if check else process

def oid(value):
    if not isinstance(value,str) or not OID.fullmatch(value):raise Rejected('Invalid object identifier.')
    return value

def identity(value,role,require_owner=False):
    match=IDENT.fullmatch(value)
    if not match or any(b in match[1] for b in (b'\r',b'\n',b'\x00')) or not EMAIL.fullmatch(match[2]):
        raise Rejected(role+': malformed identity (value withheld).')
    if blocked(match[2]):raise Rejected(role+': prohibited identity (value withheld).')
    if personal_categories(match[1]):raise Rejected(role+': protected display name requires review (value withheld).')
    if match[2].decode('utf-8','replace').casefold()==OWNER_EMAIL.casefold() and match[1].decode('utf-8','replace')!=OWNER_LOGIN:
        raise Rejected(role+': owner metadata requires the canonical public display name (value withheld).')
    if require_owner and match[2].decode('utf-8','replace').casefold()!=OWNER_EMAIL.casefold():
        raise Rejected(role+': owner checkout requires the verified noreply identity (value withheld).')

def effective():
    owner=git('config','--bool','--get','identityGuard.ownerMode',check=False).stdout.strip()==b'true'
    identity(git('var','GIT_AUTHOR_IDENT').strip(),'author')
    identity(git('var','GIT_COMMITTER_IDENT').strip(),'committer',owner)

def require_private_policy():
    location=os.environ.get('DISCOVERY_IDENTITY_POLICY_FILE','') or git('config','--get','identityGuard.policyPath',check=False).stdout.decode('utf-8','replace').strip()
    worktree=git('rev-parse','--show-toplevel',check=False)
    if location and not worktree.returncode:
        root=Path(worktree.stdout.decode('utf-8','replace').strip()).resolve();target=Path(location).resolve()
        if target==root or root in target.parents:raise Rejected('Private policy must remain outside the working repository.')
    if not personal_policy.policy_ready():
        configured=git('config','--get','identityGuard.policyPath',check=False).stdout.decode('utf-8','replace').strip()
        if configured:personal_policy.configure_policy(configured)
    if not personal_policy.policy_ready():raise Rejected('Private identity policy is required; values withheld.')

def headers(data):
    top,sep,message=data.partition(b'\n\n')
    if not sep:raise Rejected('Malformed Git object.')
    values={}
    for line in top.splitlines():
        if line.startswith(b' '):continue
        key,space,value=line.partition(b' ')
        if not space:raise Rejected('Malformed Git header.')
        if key in (b'author',b'committer',b'tagger') and key in values:raise Rejected('Duplicate identity header.')
        values[key]=value
    return values,message

def object_data(value):
    value=oid(value);kind=git('cat-file','-t',value).strip()
    if kind not in (b'commit',b'tag',b'blob',b'tree'):raise Rejected('Unexpected Git object type.')
    return kind,git('cat-file',kind.decode(),value)

def inspect_object(value,kind,data):
    if forbidden_text(data):raise Rejected(value[:12]+': prohibited identity text (value withheld).')
    if kind==b'commit':
        fields,_=headers(data)
        for name in (b'author',b'committer'):
            if name not in fields:raise Rejected(value[:12]+': missing required identity.')
            identity(fields[name],value[:12]+' '+name.decode())
    elif kind==b'tag':
        fields,_=headers(data)
        if b'tagger' in fields:identity(fields[b'tagger'],value[:12]+' tagger')

def objects_for(tip,before=None):
    oid(tip)
    args=['rev-list','--objects',tip]
    if before:
        oid(before);git('cat-file','-e',before);args+=['--not',before]
    # Object IDs alone avoid printing or interpreting untrusted file names.
    return set(line.split(b' ',1)[0].decode('ascii') for line in git(*args).splitlines())|{tip}

def audit_objects(values):
    checked=0
    # Persistent cat-file avoids spawning one process per archived object.
    process=subprocess.Popen(['git','cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        require_private_policy()
        for value in sorted(set(values)):
            oid(value);process.stdin.write((value+'\n').encode());process.stdin.flush()
            parts=process.stdout.readline().split()
            if len(parts)!=3:raise Rejected('Required object is unavailable.')
            kind,size=parts[1],int(parts[2]);data=process.stdout.read(size)
            if len(data)!=size or process.stdout.read(1)!=b'\n':raise Rejected('Truncated Git object.')
            inspect_object(value,kind,data);checked+=1
    finally:
        process.stdin.close();process.stdout.close();process.stderr.close()
        process.wait(timeout=20)
    if process.returncode:raise Rejected('Git object audit failed (details withheld).')
    return checked

def audit_range(tip,before=None):return audit_objects(objects_for(tip,before))

def staged():
    # :path is passed as a separate argument; filenames are never shell code.
    names=git('diff','--cached','--name-only','--diff-filter=ACMR','-z').split(b'\x00')
    for name in filter(None,names):
        if forbidden_text(name):raise Rejected('Staged filename contains a prohibited identity (value withheld).')
        data=git('show',b':'+name)
        if forbidden_text(data):raise Rejected('Staged file contains a prohibited identity (file/value withheld).')

def pre_push(lines):
    checked=0
    for line in lines:
        parts=line.split()
        if len(parts)!=4:raise Rejected('Malformed push description.')
        local_ref,local,remote_ref,remote=parts
        oid(local);oid(remote)
        if set(local)=={'0'}:continue
        if forbidden_text(remote_ref.encode()):raise Rejected('Destination reference contains a protected identity (value withheld).')
        checked+=audit_range(local,None if set(remote)=={'0'} else remote)
    return checked

def fetch_ref(ref,destination):
    if git('check-ref-format',ref,check=False).returncode:raise Rejected('Invalid event reference.')
    # Fixed origin and validated refs only. Never execute code from the audited tree.
    git('fetch','--no-tags','origin',ref+':'+destination)
    return git('rev-parse',destination).decode().strip()

def github_event(path,event_name):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    # Capture all fetch output so rejected reference names never reach CI logs.
    fetch=['fetch','--no-tags','--prune']
    if git('rev-parse','--is-shallow-repository').strip()==b'true':fetch.append('--unshallow')
    git(*fetch,'origin','+refs/heads/*:refs/remotes/origin/*','+refs/tags/*:refs/tags/*')
    if event_name=='pull_request_target':
        pr=value.get('pull_request',{});number=value.get('number')
        if type(number)is not int or number<1:raise Rejected('Invalid pull request event.')
        head=oid(pr.get('head',{}).get('sha'));base=oid(pr.get('base',{}).get('sha'))
        got=fetch_ref(f'refs/pull/{number}/head',f'refs/identity-audit/pr-{number}')
        if got!=head:raise Rejected('Pull request changed during audit; rerun against its current head.')
        git('cat-file','-e',base)
        return audit_range(head,base)
    if event_name=='push':
        head=oid(value.get('after'));before=oid(value.get('before'))
        if set(head)=={'0'}:return 0
        ref=value.get('ref','')
        if not ref.startswith(('refs/heads/','refs/tags/')):raise Rejected('Invalid push reference.')
        if forbidden_text(ref.encode()):raise Rejected('Push reference contains a protected identity (value withheld).')
        got=fetch_ref(ref,'refs/identity-audit/push')
        # Annotated tag payloads must be checked even when an event uses a peeled SHA.
        peeled=git('rev-parse',got+'^{commit}',check=False)
        if got!=head and (peeled.returncode or peeled.stdout.decode().strip()!=head):
            raise Rejected('Push changed during audit; rerun against its current head.')
        if set(before)!={'0'} and git('cat-file','-e',before,check=False).returncode:
            git('fetch','--no-tags','origin',before)
        return audit_range(got,None if set(before)=={'0'} else before)
    if event_name in ('workflow_dispatch','schedule'):
        return audit_objects(all_objects())
    raise Rejected('Unsupported event; audit refused.')

def all_objects():
    for ref in git('for-each-ref','--format=%(refname)').splitlines():
        if forbidden_text(ref):raise Rejected('Reference contains a protected identity (value withheld).')
    values=set(line.split(b' ',1)[0].decode() for line in git('rev-list','--objects','--all').splitlines())
    values.update(git('for-each-ref','--format=%(objectname)').decode().splitlines())
    return values

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--effective',action='store_true');p.add_argument('--staged',action='store_true')
    p.add_argument('--message',type=Path);p.add_argument('--pre-push',action='store_true')
    p.add_argument('--tip');p.add_argument('--before');p.add_argument('--all',action='store_true')
    p.add_argument('--github-event',type=Path);p.add_argument('--event-name')
    args=p.parse_args(argv)
    if not any((args.effective,args.staged,args.message,args.pre_push,args.tip,args.all,args.github_event)):p.error('Select an audit mode')
    checked=0
    try:
        require_private_policy()
        if args.effective:effective()
        if args.staged:staged()
        if args.message and forbidden_text(args.message.read_bytes()):raise Rejected('Commit message contains a prohibited identity (value withheld).')
        if args.pre_push:checked+=pre_push(sys.stdin.read().splitlines())
        if args.tip:checked+=audit_range(args.tip,args.before)
        if args.all:checked+=audit_objects(all_objects())
        if args.github_event:checked+=github_event(args.github_event,args.event_name)
    except (Rejected,OSError,ValueError,KeyError,UnicodeError) as error:
        # Never echo subprocess exceptions, object contents or untrusted exception text.
        text=str(error) if isinstance(error,Rejected) else 'Identity audit could not complete (details withheld).'
        print('Identity guard: REJECTED: '+text,file=sys.stderr);return 1
    print(f'Identity guard: PASS; {checked} objects checked; identity values withheld.');return 0

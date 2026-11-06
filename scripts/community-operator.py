"""Restricted operator workflow. Credentials come only from libpq environment.

Use a dedicated, mapped operator login. Never use postgres/service_role. Remote
operation additionally requires --allow-remote and PGSSLMODE=verify-full; creating
this tool does not authorize any hosted operation.
"""
import argparse
import json
import os
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['configure','invite','revoke_invite','block','unblock',
        'hide','remove','resolve_report','dismiss_report','reports','audit'])
    parser.add_argument('--target',type=int)
    parser.add_argument('--reason')
    parser.add_argument('--until',help='UTC invitation expiry, at most 30 days ahead')
    parser.add_argument('--allow-remote',action='store_true')
    args=parser.parse_args()
    if os.environ.get('PGUSER') in (None,'postgres','service_role','authenticated','anon'):
        parser.error('A separately provisioned restricted operator login is required')
    if os.environ.get('PGHOST') not in ('127.0.0.1','localhost','::1'):
        if not args.allow_remote or os.environ.get('PGSSLMODE')!='verify-full':
            parser.error('Remote operations require separate approval, --allow-remote and verified TLS')
    def literal(value):return 'null' if value is None else "E'"+str(value).replace('\\','\\\\').replace("'","''")+"'"
    statement='set role community_operator; select public.community_operator('+','.join(
        literal(v) for v in (args.action,args.target,args.reason,args.until))+');'
    result=subprocess.run(['psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1'],input=statement,
        capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        print('Operator action denied or unavailable; no SQL/credential diagnostics printed.',file=sys.stderr)
        return 1
    try:value=json.loads(result.stdout.strip())
    except ValueError:
        print('Unexpected operator response; investigate without retrying mutations.',file=sys.stderr);return 1
    print(json.dumps(value));return 0


if __name__=='__main__':raise SystemExit(main())

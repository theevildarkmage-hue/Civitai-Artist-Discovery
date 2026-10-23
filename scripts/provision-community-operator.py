"""Admin-only operator credential lifecycle; never emits credential material.

PG* supplies the approved admin connection. COMMUNITY_OPERATOR_NEW_PASSWORD comes
from an approved secret manager. This tool grants no permission to use a host.
The authenticating admin must already have a verified private operator mapping.
"""
import argparse
from datetime import datetime,timezone
import json
import os
import re
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','rotate','disable','revoke'])
    parser.add_argument('--login',required=True)
    parser.add_argument('--member-id',type=int,help='Verified stable identity for new enrollment only')
    parser.add_argument('--until',help='UTC expiry, required for create/rotate, maximum 30 days')
    parser.add_argument('--reason',required=True)
    parser.add_argument('--allow-remote',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'cad3_operator_[a-z0-9_]{1,32}',args.login):parser.error('Restricted operator login prefix required')
    if not 3<=len(args.reason)<=256:parser.error('A bounded audit reason is required')
    if args.login==os.environ.get('PGUSER'):parser.error('Self credential lifecycle is prohibited')
    if os.environ.get('PGHOST') not in ('127.0.0.1','localhost','::1'):
        if not args.allow_remote or os.environ.get('PGSSLMODE')!='verify-full':parser.error('Separate remote approval and verified TLS required')
    if args.action=='create' and (not args.member_id or args.member_id<=0):parser.error('Verified enrollment ID required')
    if args.action!='create' and args.member_id is not None:parser.error('Rotation/revocation cannot remap identity')
    password=os.environ.get('COMMUNITY_OPERATOR_NEW_PASSWORD')
    if args.action in ('create','rotate'):
        if not password or not 32<=len(password)<=256 or any(ord(c)<32 for c in password):parser.error('New credential must be securely supplied, minimum 32 characters')
        try:until=datetime.fromisoformat(args.until.replace('Z','+00:00'))
        except (ValueError,AttributeError):parser.error('UTC expiry required')
        if until.utcoffset() is None or until.utcoffset().total_seconds()!=0:parser.error('UTC expiry required')
        remaining=(until-datetime.now(timezone.utc)).total_seconds()
        if not 0<remaining<=30*86400:parser.error('Expiry must be within 30 days')
    def lit(value):return 'null' if value is None else "E'"+str(value).replace('\\','\\\\').replace("'","''")+"'"
    login='"'+args.login+'"';target=lit(args.login)
    actor="(select actor_id from community_private.operators where login=session_user and not revoked and expires_at>clock_timestamp())"
    prefix="""begin;
set local log_statement='none';set local log_min_error_statement='panic';
select pg_advisory_xact_lock(7530230001);
do $$ begin
 if not exists(select 1 from pg_roles where rolname=session_user and (rolsuper or rolcreaterole)) then raise exception 'Admin authority required';end if;
 if not exists(select 1 from community_private.operators where login=session_user and not revoked and expires_at>clock_timestamp()) then raise exception 'Verified admin mapping required';end if;
 if not exists(select 1 from pg_roles where rolname='community_operator' and not rolcanlogin and not rolinherit and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb and not rolreplication) then raise exception 'Restricted operator group required';end if;
 if exists(select 1 from pg_auth_members where member=(select oid from pg_roles where rolname='community_operator')) then raise exception 'Unexpected operator group authority';end if;
 if coalesce(current_setting('pgaudit.log',true),'none') not in ('','none') then raise exception 'Review credential-safe audit transport before provisioning';end if;
end $$;
"""
    audit=lambda action:"insert into community_private.moderator_audit(actor_id,actor_login,action,target,reason) values("+actor+",session_user,"+lit(action)+",(select actor_id from community_private.operators where login="+target+"),"+lit(args.reason)+");\n"
    def execute(statement):
        result=subprocess.run(['psql','-X','-qAt','-v','ON_ERROR_STOP=1'],input=statement,
            capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode:
            print('Operator lifecycle stopped; credential/SQL details withheld. Target may remain disabled.',file=sys.stderr)
            return False
        return True
    if args.action=='create':
        statement=prefix+'create role '+login+' login noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls password '+lit(password)+' valid until '+lit(args.until)+';\n'
        statement+='grant community_operator to '+login+';grant connect on database "'+os.environ['PGDATABASE'].replace('"','""')+'" to '+login+';\n'
        statement+='insert into community_private.operators values('+target+','+str(args.member_id)+','+lit(args.until)+',false);\n'+audit('operator_create')+'commit;'
        if not execute(statement):return 1
    else:
        # Disable first; interrupted rotation remains denied rather than reviving
        # the old credential. Existing sessions lose RPC authority immediately.
        statement=prefix+"do $$ begin if not exists(select 1 from community_private.operators where login="+target+") then raise exception 'Unknown operator';end if;end $$;\n"
        statement+='alter role '+login+' nologin;update community_private.operators set revoked=true where login='+target+';\n'
        if args.action=='revoke':statement+='alter role '+login+' password null;revoke community_operator from '+login+';\n'
        statement+=audit('operator_'+('disable' if args.action=='rotate' else args.action))+'commit;'
        if not execute(statement):return 1
        terminate="select pg_terminate_backend(pid) from pg_stat_activity where usename="+target+" and datname=current_database() and pid<>pg_backend_pid();"
        if not execute(terminate):return 1
        if args.action=='rotate':
            statement=prefix+'alter role '+login+' password '+lit(password)+' valid until '+lit(args.until)+' login;\n'
            statement+='grant community_operator to '+login+';update community_private.operators set revoked=false,expires_at='+lit(args.until)+' where login='+target+';\n'
            statement+=audit('operator_rotate')+'commit;'
            if not execute(statement):return 1
    print(json.dumps({'completed':True,'action':args.action,'login':args.login,'credentialDisplayed':False}));return 0


if __name__=='__main__':raise SystemExit(main())

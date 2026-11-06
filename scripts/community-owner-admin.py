"""Local owner administration through project-scoped Management SQL.

Run with the existing Credential Manager secure environment launcher. This uses
infrastructure-owner authority, not independently verified moderator identity.
No password, Auth user, public admin RPC, or application credential is created.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REF = 'aibelmhucizupzvvhdvf'
ACTIONS = ('configure', 'invite', 'revoke_invite', 'invitation_status', 'block',
           'unblock', 'hide', 'remove', 'resolve_report', 'dismiss_report',
           'reports', 'audit', 'registry_stats', 'registry_delete', 'registry_retention')


def literal(value):
    # UTF-8 hex avoids SQL quoting, backslash, encoding and dollar-tag ambiguity.
    return 'null' if value is None else "convert_from(decode('" + str(value).encode().hex() + "','hex'),'UTF8')"


def statement(action, target, reason, until=None, *, rollback=False):
    if action not in ACTIONS or not isinstance(reason, str) or not 3 <= len(reason) <= 256:
        raise ValueError('Invalid action/reason')
    if target is not None and (type(target) is not int or target <= 0):
        raise ValueError('Invalid target')
    args = [literal(action), 'null' if target is None else str(target), literal(reason), literal(until) + '::timestamptz']
    return ("begin;set local lock_timeout='5s';set local statement_timeout='15s';"
            'select community_private.owner_admin(' + ','.join(args) + ') as result;'
            + ('rollback;' if rollback else 'commit;'))


class Management:
    def __init__(self, token):
        self.token = token
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path='', query=None, *, read_only=True):
        if path not in ('', '/database/query', '/database/query/read-only'):
            raise ValueError('Unapproved management operation')
        body = None if query is None else json.dumps({'query': query, **({'read_only': read_only} if path == '/database/query' else {})}).encode()
        request = urllib.request.Request('https://api.supabase.com/v1/projects/' + REF + path,
                    data=body, headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise RuntimeError('Oversized management response; do not retry mutations')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(f'Management operation failed (HTTP {code}); response withheld; do not retry mutations') from None
        except (OSError, ValueError):
            raise RuntimeError('Management operation outcome unavailable; inspect before retrying') from None

    def verify(self):
        project = self.request()
        if project.get('id') != REF or project.get('region') != 'us-west-2' or project.get('status') != 'ACTIVE_HEALTHY':
            raise RuntimeError('Project identity/health mismatch')
        rows = self.request('/database/query', "select current_user,session_user,current_database(),current_setting('transaction_read_only') as read_only", read_only=False)
        if rows != [{'current_user': 'postgres', 'session_user': 'postgres', 'current_database': 'postgres', 'read_only': 'off'}]:
            raise RuntimeError('Owner SQL execution authority mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=ACTIONS)
    parser.add_argument('--target', type=int)
    parser.add_argument('--profile', help='Owner-approved Civitai profile; required for invite')
    parser.add_argument('--reason', required=True, help='Nonsecret purpose, 3–256 characters')
    parser.add_argument('--until')
    parser.add_argument('--rollback', action='store_true', help='Execute then roll back all data/audit changes')
    parser.add_argument('--execute', action='store_true', help='Explicitly execute; all actions including review are audited')
    args = parser.parse_args()
    if not args.execute:
        parser.error('Explicit --execute required; no request sent')
    token = os.environ.get('SUPABASE_ACCESS_TOKEN')
    if not token:
        parser.error('Use the existing secure Credential Manager launcher; never paste credentials')
    if args.action == 'invite':
        if not args.profile or args.target is not None:
            parser.error('Invitation requires an owner-approved profile, not an unverified raw ID')
        from discovery.invitations import resolve_account
        args.target = resolve_account(args.profile)['id']
    elif args.profile:
        parser.error('--profile is only valid for invite')
    query = statement(args.action, args.target, args.reason, args.until, rollback=args.rollback)
    manager = Management(token)
    manager.verify()
    permissions = manager.request('/database/query/read-only', "select bool_and(not has_function_privilege(r,'community_private.owner_admin(text,bigint,text,timestamptz)','execute') and not has_schema_privilege(r,'community_private','usage')) as denied from unnest(array['anon','authenticated','service_role','community_operator']) r")
    if permissions != [{'denied': True}]:
        raise RuntimeError('Client administration isolation mismatch; no action attempted')
    result = manager.request('/database/query', query, read_only=False)
    print(json.dumps({'ownerActor': 'owner-admin:discovery3', 'independentModeratorAttribution': False,
                      'rolledBack': args.rollback, 'response': result}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error) if isinstance(error, (RuntimeError, ValueError)) else 'Owner action stopped; diagnostics withheld', file=sys.stderr)
        raise SystemExit(1)

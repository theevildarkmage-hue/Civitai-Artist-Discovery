"""Policy simulation only in the native runner's attested disposable cluster."""
import os,json,subprocess
from pathlib import Path

def run():
    if os.environ.get('CAD_ALLOW_ISOLATED_DB_TESTS')!='1':
        print('SKIP: owned native runner with --community-access required');return
    env=os.environ.copy()
    def sql(value):
        return subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1'],input=value,env=env,capture_output=True,text=True,timeout=30)
    assert env['PGHOST']=='127.0.0.1'
    assert sql('select system_identifier from pg_control_system();').stdout.strip()==env['CAD_TEST_PG_SYSTEM_IDENTIFIER']
    assert Path(sql('show data_directory;').stdout.strip()).resolve()==Path(env['CAD_TEST_PG_DATA_DIR']).resolve()
    body="""
begin;
create temporary table preservation as select
 (select count(*) from community_recommendations) recs,
 (select count(*) from community_audit) audits;
update community_private.configuration set ready=true;
update community_private.invitations set revoked=true;
do $$begin
 if (select access_mode from community_private.configuration where singleton)<>'invite_only' then raise exception 'default';end if;
 begin perform community_access(82000001);raise exception 'unexpected';exception when others then if sqlerrm<>'INVITE_ONLY' then raise;end if;end;
 insert into community_private.invitations values(82000001,clock_timestamp()+interval '1 hour',false);
 perform community_access(82000001);
 update community_private.invitations set revoked=true where member_id=82000001;
 begin perform community_access(82000001);raise exception 'unexpected';exception when others then if sqlerrm<>'INVITE_REVOKED' then raise;end if;end;
 update community_private.invitations set revoked=false,expires_at=clock_timestamp()-interval '1 minute' where member_id=82000001;
 begin perform community_access(82000001);raise exception 'unexpected';exception when others then if sqlerrm<>'INVITE_EXPIRED' then raise;end if;end;
end $$;
select community_private.owner_access_policy('public','Isolated policy acceptance');
do $$declare i integer; m bigint;begin
 if (community_access(82000001)->>'accessMode')<>'public' then raise exception 'public access';end if;
 perform community_access(82000002); -- No invitation or manual enrollment.
 perform community_beta_gallery(82000002,null,24);
 for m in select unnest(array[82000001::bigint,11107642::bigint]) loop
   for i in 1..3 loop
     perform community_submit(m,'Synthetic',m*100+i,83000000+m*10+i,false);
   end loop;
   begin perform community_submit(m,'Synthetic',m*100+4,83000000+m*10+4,false);raise exception 'unexpected';
   exception when others then if sqlerrm<>'MEMBER_QUOTA' then raise;end if;end;
   if (community_quota(m)->>'limit')::int<>3 or (community_quota(m)->>'unlimited')::boolean then raise exception 'public quota';end if;
 end loop;
 begin perform community_submit(82000002,'Synthetic',82000002::bigint*100,82000002,false);raise exception 'unexpected';
 exception when others then if sqlerrm<>'SELF_RECOMMENDATION' then raise;end if;end;
 begin perform community_submit(82000002,'Synthetic',82000001::bigint*100+1,999,false);raise exception 'unexpected';
 exception when others then if sqlerrm<>'DUPLICATE' then raise;end if;end;
 insert into community_private.blocks values(82000002,true);
 begin perform community_access(82000002);raise exception 'unexpected';exception when others then if sqlerrm<>'SUBMITTER_BLOCKED' then raise;end if;end;
 begin perform community_submit(82000002,'Synthetic',99000001,99000002,false);raise exception 'unexpected';
 exception when others then if sqlerrm<>'SUBMITTER_BLOCKED' then raise;end if;end;
end $$;
select community_private.owner_access_policy('invite_only','Restore isolated beta policy');
do $$begin
 begin perform community_access(82000002);raise exception 'unexpected';exception when others then if sqlerrm<>'SUBMITTER_BLOCKED' then raise;end if;end;
 begin perform community_access(82000001);raise exception 'unexpected';exception when others then if sqlerrm<>'INVITE_EXPIRED' then raise;end if;end;
 if not exists(select 1 from community_private.moderator_audit where action='community_access_policy:public' and actor_login='owner-admin:discovery3' and reason='Isolated policy acceptance') then raise exception 'audit';end if;
end $$;
update community_private.configuration set ready=false;
do $$begin
 begin perform community_access(82000001);raise exception 'unexpected';exception when others then if sqlerrm<>'BETA_DISABLED' then raise;end if;end;
end $$;
rollback;
"""
    result=sql(body)
    assert result.returncode==0,result.stderr
    credentials=json.loads(env['CAD_TEST_PG_ROLE_PASSWORDS']);denials=0
    for role in ('anon','authenticated','service_role'):
        for statement in ("select community_private.owner_access_policy('public','Forged request');",
                          "update community_private.configuration set access_mode='public';"):
            result=subprocess.run(['psql','-X','-A','-t','-v','ON_ERROR_STOP=1','-c',statement],env={**env,'PGUSER':role,'PGPASSWORD':credentials[role]},capture_output=True,text=True,timeout=20)
            assert result.returncode!=0 and 'permission denied' in result.stderr;denials+=1
    assert sql("select access_mode from community_private.configuration where singleton;").stdout.strip()=='invite_only'
    assert sql("select count(*) from community_recommendations where member_id=82000001;").stdout.strip()=='0'
    print(json.dumps({'passed':True,'nativeSQL':True,'publicPolicySimulated':True,'ordinaryAndOwnerPublicCaps':3,'untrustedPolicyDenials':denials,'allFixtureChangesRolledBack':True,'hostedRequests':0}))

if __name__=='__main__':run()

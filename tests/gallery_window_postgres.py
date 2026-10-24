"""Native candidate migration and sixty expired rows; every fixture rolls back."""
from pathlib import Path
import json,importlib.util

def run(sql,output):
    root=Path(__file__).resolve().parents[1]
    path=root/'supabase/beta-migrations/202610240002_gallery_review_window.sql'
    source=path.read_text(encoding='utf-8')
    before=sql('select count(*) from community_recommendations;')
    spec=importlib.util.spec_from_file_location('gallery_builder',root/'scripts/prepare-gallery-candidate.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    bundle=builder.build_atomic(expected_owner=sql('select current_user;'),expected_database=sql('select current_database();'))
    sql(bundle.removesuffix('commit;\n')+'rollback;\n')
    assert sql("select to_regprocedure('public.community_prepare_gallery(bigint,bigint,integer)') is null and (select count(*) from supabase_migrations.schema_migrations)=9;")=='t'
    assert sql('select count(*) from community_recommendations;')==before
    sql(bundle)
    (output/'rehearsed-gallery-upgrade.sql').write_text(bundle,encoding='utf-8')
    fixture="""begin;delete from community_private.invitations;
      insert into community_members values(8930000000,'SyntheticGalleryWindow');
      insert into community_private.invitations values(8930000000,clock_timestamp()+interval '1 day',false);
      update community_private.configuration set ready=true;
      insert into community_recommendations(image_id,creator_id,member_id,week)
        select 8931000000+n,8932000000+n,8930000000,date_trunc('week',clock_timestamp() at time zone 'UTC')::date from generate_series(0,59) n;
      insert into community_private.artwork_reviews(image_id,state,checked_at,valid_until) select 8931000000+n,'eligible',clock_timestamp()-interval '1 day',clock_timestamp()-interval '1 day' from generate_series(0,59)n;
      update community_private.artwork_reviews set valid_until=clock_timestamp()-interval '1 day',lease_until=null where image_id between 8931000000 and 8931000059;
      do $test$ declare cursor bigint;page jsonb;batch jsonb;item jsonb;seen bigint[]='{}';calls integer=0;previous bigint;begin
        loop
          batch=community_prepare_gallery(8930000000,cursor,24);
          if jsonb_array_length(batch)>3 then raise exception 'UNBOUNDED_REVIEW';end if;
          for item in select * from jsonb_array_elements(batch) loop
            perform community_record_review(8930000000,(item->>'imageId')::bigint,(item->>'creatorId')::bigint,(item->>'lease')::uuid,null);
          end loop;
          page=community_beta_gallery(8930000000,cursor,24);
          for item in select * from jsonb_array_elements(page->'items') loop
            if (item->>'imageId')::bigint between 8931000000 and 8931000059 then
              if (item->>'imageId')::bigint=any(seen) then raise exception 'DUPLICATE';end if;
              if previous is not null and (item->>'imageId')::bigint>=previous then raise exception 'ORDER';end if;
              previous=(item->>'imageId')::bigint;seen=array_append(seen,previous);
            end if;
          end loop;
          calls=calls+1;
          if cardinality(seen)=60 then exit;end if;
          if page->>'nextCursor' is null then raise exception 'PREMATURE_END';end if;
          if cursor is not null and (page->>'nextCursor')::bigint>=cursor then raise exception 'CURSOR_NOT_ADVANCING';end if;
          cursor=(page->>'nextCursor')::bigint;
          if calls>21 then raise exception 'UNBOUNDED_PAGES';end if;
        end loop;
        if calls<>20 then raise exception 'COLD_BATCH_SIZE';end if;
        page=community_beta_gallery(8930000000,null,24);
        if jsonb_array_length(page->'items')<>24 then raise exception 'WARM_PAGE_SIZE';end if;
      end $test$;
      do $test$ declare batch jsonb;lease uuid;snapshot jsonb;begin
        update community_private.artwork_reviews set valid_until=clock_timestamp()-interval '1 second',lease_until=null where image_id=8931000059;
        select jsonb_build_array(state,checked_at,valid_until,failure) into snapshot from community_private.artwork_reviews where image_id=8931000059;
        batch=community_prepare_gallery(8930000000,null,24);lease=(batch->0->>'lease')::uuid;
        perform community_record_review(8930000000,8931000059,8932000059,lease,'viewer');
        if snapshot is distinct from (select jsonb_build_array(state,checked_at,valid_until,failure) from community_private.artwork_reviews where image_id=8931000059) then raise exception 'VIEWER_CHANGED_GLOBAL_VERDICT';end if;
        batch=community_prepare_gallery(8930000000,null,24);
        if jsonb_array_length(batch)<>1 then raise exception 'VIEWER_LOCKED_OUT_NEXT_REVIEWER';end if;
        lease=(batch->0->>'lease')::uuid;
        perform community_record_review(8930000000,8931000059,8932000059,lease,'rating');
        if (select state from community_private.artwork_reviews where image_id=8931000059)<>'quarantined' then raise exception 'PUBLIC_RATING_NOT_QUARANTINED';end if;
        if exists(select 1 from jsonb_array_elements(community_beta_gallery(8930000000,null,24)->'items') x where x->>'imageId'='8931000059') then raise exception 'UNSAFE_ARTWORK_RETURNED';end if;
      end $test$;
      rollback;"""
    sql(fixture)
    assert sql('select count(*) from community_recommendations;')==before
    for role in ('anon','authenticated','service_role'):
        assert sql(f"select not has_function_privilege('{role}','community_private.gallery_window(bigint,integer)','execute');")=='t'
    for role in ('anon','authenticated'):
        assert sql(f"select not has_function_privilege('{role}','public.community_prepare_gallery(bigint,bigint,integer)','execute');")=='t'
    result={'passed':True,'realPostgreSQL':True,'atomicRollbackAndRetry':True,'syntheticRows':60,'coldPages':20,'reviewBatchMaximum':3,'warmPageSize':24,
      'allSixtyReachableWithoutDuplicates':True,'cursorAdvancesAcrossExpiredRows':True,'viewerFailurePreservesGlobalVerdictAndReleasesLease':True,
      'publicRatingChangeFailsClosed':True,'newRpcUntrustedDenials':True,'recommendationsPersisted':0,'hostedMutation':False}
    (output/'gallery-window.json').write_text(json.dumps(result,indent=2));return result

if __name__=='__main__':print('SKIP: run through owned native release runner')

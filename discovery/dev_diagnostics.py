"""Read-only development snapshots. No token payloads, refresh, sync or ledger writes."""
from __future__ import annotations
import json
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from .diagnostics import redact_sensitive


def enabled():
    return os.environ.get('CAD_DEVELOPMENT')=='1' and not getattr(sys,'frozen',False)


def git_commit(root):
    try:
        return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,
            stderr=subprocess.DEVNULL,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
    except (OSError,subprocess.SubprocessError):return 'unavailable'


def rows(path,query,args=()):
    if not Path(path).is_file():return []
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        return [dict(row) for row in db.execute(query,args)]
    except sqlite3.Error:return []
    finally:db.close()


def connectivity():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    result={}
    endpoints={'Civitai API':'https://civitai.red/api/v1/images?limit=1&browsingLevel=1',
               'Civitai OAuth':'https://auth.civitai.com/.well-known/openid-configuration'}
    for label,url in endpoints.items():
        try:
            request=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'Discovery3-Development-Diagnostics'})
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=2) as response:
                status=response.status
        except urllib.error.HTTPError as error:
            status=error.code;error.close()
        except Exception:
            result[label]={'state':'unavailable','note':'No response within the bounded probe; cached data is unchanged.'};continue
        result[label]={'state':'reachable' if status<500 else 'service error','httpStatus':status,
            'note':'Unauthenticated HEAD; does not verify OAuth grants or artwork eligibility.'}
    return result


def snapshot(root,data,version,commit,*,auth_status,job,probe=False):
    root=Path(root);data=Path(data);now=time.time()
    auth={}
    # auth_status reads protected cached state only; session_status could start refresh.
    try:
        auth=auth_status()
        authentication={'connected':bool(auth.get('connected')),'state':'cached session' if auth.get('connected') else 'reauthentication required',
            'readOnly':not auth.get('socialWrite',False),'verification':'cached state; no token refresh or live identity check'}
    except Exception:authentication={'connected':False,'state':'no usable cached session','verification':'no refresh attempted'}
    taste=data/'discovery/taste.sqlite3'
    counts={}
    for table in ('reacted_images','reacted_reactions','followed_creators'):
        found=rows(taste,f'SELECT count(*) AS count FROM {table}')
        counts[table]=found[0]['count'] if found else 0
    state={r['key']:r['value'] for r in rows(taste,"SELECT key,value FROM sync_state WHERE key IN ('last_sync_at','last_attempt_at','sync_checkpoint')")}
    cache=data/'cache/community-metadata.sqlite3'
    records=rows(cache,'SELECT key,value,verified_at FROM metadata_cache')
    cache_status={'entries':len(records),'fresh':sum(now-r['verified_at']<86400 for r in records),
        'stale':sum(now-r['verified_at']>=86400 for r in records),
        'lastVerifiedAt':max((r['verified_at'] for r in records),default=None),'freshnessSeconds':86400}
    mode=os.environ.get('CAD_COMMUNITY_MODE','offline')
    ledger=data/'community'/('fixtures.sqlite3' if mode=='fixture' else 'local.sqlite3')
    community={'mode':mode,'recommendations':0,'auditRecords':0}
    for table,label in (('recommendations','recommendations'),('audit','auditRecords')):
        found=rows(ledger,f'SELECT count(*) AS count FROM {table}')
        community[label]=found[0]['count'] if found else 0
    # Reconcile only cached local artwork against current cached controls: no verifier call.
    from .community import Artwork, ContentProfile, content_profile
    hidden_creators=rows(taste,'SELECT creator_id,username_key,reason FROM hidden_creators')
    hidden_images=rows(taste,'SELECT image_id FROM hidden_images')
    hidden_tags=rows(taste,'SELECT tag_id,tag_name FROM hidden_tags')
    try:settings=json.loads((data/'settings.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):settings={}
    profile=ContentProfile(levels=frozenset(settings.get('browsingLevels',[1,2])),
        apply_tag_filters=settings.get('applyTagFilters',True) is not False,
        apply_account_filters=settings.get('applyAccountFilters',True) is not False,
        apply_image_filters=settings.get('applyImageFilters',True) is not False,
        creators=frozenset(r['creator_id'] for r in hidden_creators),
        usernames=frozenset(r['username_key'] for r in hidden_creators if r['username_key']),
        images=frozenset(r['image_id'] for r in hidden_images),
        tags=frozenset(r['tag_name'] for r in hidden_tags),tag_ids=frozenset(r['tag_id'] for r in hidden_tags),
        blocked_creators=frozenset(r['creator_id'] for r in hidden_creators if r['reason']=='blocked'),
        blocked_usernames=frozenset(r['username_key'] for r in hidden_creators if r['reason']=='blocked' and r['username_key']))
    for record in records:
        if record['key']==f"controls:{auth.get('id')}":
            try:profile=replace(content_profile(json.loads(record['value']),profile.levels),
                apply_tag_filters=profile.apply_tag_filters, apply_account_filters=profile.apply_account_filters,
                apply_image_filters=profile.apply_image_filters)
            except Exception:pass
    cached_art={}
    for record in records:
        if record['key'].startswith(mode+':artwork:'):
            try:
                value=json.loads(record['value']);art=Artwork(**value);cached_art[art.id]=art
            except (TypeError,ValueError):pass
    exclusions={};visible=missing=0
    for row in rows(ledger,"SELECT image_id FROM recommendations WHERE status='active'"):
        art=cached_art.get(row['image_id'])
        if art is None:missing+=1;continue
        reasons=profile.exclusions(art)
        if not reasons:visible+=1
        for reason in reasons:exclusions[reason]=exclusions.get(reason,0)+1
    community['cachedContentControls']={'visible':visible,'missingMetadata':missing,'exclusions':exclusions,
        'note':'Cached-only reconciliation; individual reasons may overlap.'}
    history=data/'history/history.sqlite3'
    gallery={'artworkRows':0,'completedBlocks':0,'latestCompleted':None}
    found=rows(history,"SELECT count(*) AS count FROM images WHERE url NOT LIKE '%/community-fixture.svg%'")
    if found:gallery['artworkRows']=found[0]['count']
    days=rows(history,'SELECT day FROM days WHERE complete=1 ORDER BY day DESC')
    real_days=rows(history,"""SELECT d.day FROM days d WHERE d.complete=1
        AND EXISTS(SELECT 1 FROM block_images b JOIN images i ON i.id=b.image_id
                   WHERE b.block_key=d.day AND i.url NOT LIKE '%/community-fixture.svg%')
        AND NOT EXISTS(SELECT 1 FROM block_images b JOIN images i ON i.id=b.image_id
                       WHERE b.block_key=d.day AND i.url LIKE '%/community-fixture.svg%')
        ORDER BY substr(d.day,1,10) DESC,instr(d.day,'#')>0,d.day DESC""")
    gallery.update(completedBlocks=len(days),realCompletedBlocks=len(real_days),
        latestCompleted=real_days[0]['day'] if real_days else None)
    errors=[];log=data/'error.log'
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0,log.stat().st_size-8192));tail=stream.read(8192).decode('utf-8','replace')
        errors=[redact_sensitive(line)[:500] for line in tail.splitlines()[-12:] if line.strip()]
    return {'development':True,'version':version,'gitCommit':commit,'processId':os.getpid(),
        'sourceDirectory':str(root.resolve()),'dataDirectory':str(data.resolve()),'authentication':authentication,
        'profile':{**counts,'cached':counts['reacted_images']>0,'lastSuccessfulSync':state.get('last_sync_at'),
            'lastAttempt':state.get('last_attempt_at'),'resumable':bool(state.get('sync_checkpoint')),
            'background':{k:job.get(k) for k in ('running','phase','images','pages')}},
        'metadataCache':cache_status,'dailyGallery':gallery,'community':community,'recentErrors':errors,
        'contentControls':{'levels':sorted(profile.levels),'hiddenCreators':len(profile.creators-profile.blocked_creators),
            'blockedUsers':len(profile.blocked_creators),'hiddenTags':len(profile.tags),'hiddenArtworks':len(profile.images)},
        'connectivity':connectivity() if probe else {'state':'not probed','note':'Run Diagnostics sends two bounded unauthenticated HEAD requests.'},
        'readOnly':True}

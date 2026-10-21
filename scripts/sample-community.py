"""Extract a bounded, offline metadata sample without opening source DBs for writes.

No tokens, prompts, reactions themselves, account config or binaries are exported.
The derived manifest is private, ignored development data. Cached IDs are fixture
evidence only, never a production ownership attestation.
"""
from __future__ import annotations
import argparse
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]


def read_db(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def sample(source, destination):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if ROOT not in destination.parents or source==destination or source in destination.parents:
        raise ValueError('Sample destination must be in the separate 3.0 workspace')
    history=source/'history/history.sqlite3'; taste=source/'discovery/taste.sqlite3'
    before={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in (history,taste)}
    records={}; stats={}; seen_creators={}
    with closing(read_db(taste)) as db:
        stats['reactionMetadataRows']=db.execute('SELECT count(*) FROM reacted_images').fetchone()[0]
        rows=db.execute('''SELECT image_id,creator_id,creator_username,nsfw_level,post_id,created_at,base_model
          FROM reacted_images WHERE creator_id>0 AND creator_username IS NOT NULL
          AND nsfw_level IN (1,2,4,8,16) ORDER BY image_id''')
        counts={level:0 for level in (1,2,4,8,16)}
        for row in rows:
            level=row['nsfw_level']; creator=row['creator_id']
            if counts[level]>=30 or seen_creators.get((level,creator),0)>=5:continue
            image=row['image_id']; counts[level]+=1;seen_creators[level,creator]=seen_creators.get((level,creator),0)+1
            tags=[{'id':r[0],'name':r[1]} for r in db.execute(
                'SELECT tag_id,tag_name FROM reacted_tags WHERE image_id=? ORDER BY tag_id LIMIT 30',(image,))]
            records[image]={'id':image,'creatorId':creator,'username':row['creator_username'],
                'browsingLevel':level,'createdAt':row['created_at'],'postId':row['post_id'],
                'baseModel':row['base_model'],'tags':tags,'source':'reaction-metadata',
                'ownerEvidence':'cached-explicit-image-owner-id','available':True}
        stats['reactionSampleRatings']={str(k):v for k,v in counts.items()}
    with closing(read_db(history)) as db:
        stats['archiveRows']=db.execute('SELECT count(*) FROM images').fetchone()[0]
        stats['archiveColumns']=[r[1] for r in db.execute('PRAGMA table_info(images)')]
        # Only fetch 250 candidates per rating; never copy/scan all image payloads.
        for level in (1,2,4,8,16):
            rows=db.execute('''SELECT id,username,browsing_level,created_at,post_id,base_model,url,width,height
              FROM images WHERE browsing_level=? AND type='image' ORDER BY id LIMIT 250''',(level,))
            kept=0; per_creator={}
            for row in rows:
                if row['id'] in records:
                    records[row['id']].update(sourcePreview=row['url'],width=row['width'],height=row['height'])
                    continue
                if kept>=12 or per_creator.get(row['username'],0)>=4:continue
                # Archive has usernames but no numeric owner column. Never infer an
                # authoritative image owner by joining a mutable username to a profile.
                kept+=1;per_creator[row['username']]=per_creator.get(row['username'],0)+1
                image=row['id']
                simulated_owner=8_000_000_000+int(hashlib.sha256(row['username'].encode()).hexdigest()[:8],16)
                records[image]={'id':image,'creatorId':simulated_owner,
                    'username':row['username'],'browsingLevel':row['browsing_level'],
                    'createdAt':row['created_at'],'postId':row['post_id'],'baseModel':row['base_model'],
                    'sourcePreview':row['url'],'width':row['width'],'height':row['height'],
                    'tags':[],'source':'archive-metadata','ownerEvidence':'explicit-simulated-owner',
                    'available':True}
        # Resolve cached archive metadata for reacted IDs with indexed lookups only.
        for image,record in records.items():
            if record['source']!='reaction-metadata':continue
            row=db.execute('SELECT url,width,height,type FROM images WHERE id=?',(image,)).fetchone()
            if row and row['type']=='image':record.update(sourcePreview=row['url'],width=row['width'],height=row['height'])
    after={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in (history,taste)}
    stats['sourceFilesUnchangedDuringSampling']=before==after
    stats['sampleRows']=len(records)
    stats['sampleRatings']={str(level):sum(r['browsingLevel']==level for r in records.values()) for level in (1,2,4,8,16)}
    # Known negative states are synthetic modifications of copied metadata only;
    # offline cached metadata cannot establish live deletion or accessibility.
    negatives=[{'id':9_000_000_001,'state':'deleted'}, {'id':9_000_000_002,'state':'inaccessible'},
               {'id':9_000_000_003,'state':'missing_creator'}, {'id':9_000_000_004,'state':'unverified_owner'}]
    manifest={'schemaVersion':1,'developmentOnly':True,'sourceStats':stats,
              'artworks':sorted(records.values(),key=lambda r:r['id']),'negativeCases':negatives}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(stats,indent=2))
    return manifest


def enrich_tags(source,destination):
    """Add complete cached tag inventories to existing public sample rows only."""
    destination=Path(destination).resolve()
    if ROOT not in destination.parents:raise ValueError('Sample must remain in the 3.0 workspace')
    path=Path(source).resolve()/'discovery'/'taste.sqlite3'
    before=(path.stat().st_size,path.stat().st_mtime_ns)
    manifest=json.loads(destination.read_text(encoding='utf-8'));added=0
    with closing(read_db(path)) as db:
        for row in manifest['artworks']:
            image=row['id']
            if row['source']=='reaction-metadata':
                tags=[{'id':r[0],'name':r[1]} for r in db.execute('SELECT tag_id,tag_name FROM reacted_tags WHERE image_id=?',(image,))]
                if not tags:continue
            elif db.execute('SELECT 1 FROM archive_image_seen WHERE image_id=?',(image,)).fetchone():
                tags=[{'name':r[0]} for r in db.execute('SELECT tag_name FROM archive_image_tags WHERE image_id=?',(image,))]
            else:continue
            row['tags']=tags;row['tagsComplete']=True;added+=1
    assert before==(path.stat().st_size,path.stat().st_mtime_ns),'Source changed during read'
    temporary=destination.with_suffix('.tmp');temporary.write_text(json.dumps(manifest,indent=2),encoding='utf-8');temporary.replace(destination)
    print(json.dumps({'tagInventoriesAdded':added,'sourceUnchanged':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'data-test-community/sample.json')
    parser.add_argument('--enrich-tags',action='store_true')
    args=parser.parse_args()
    (enrich_tags if args.enrich_tags else sample)(args.source,args.output)

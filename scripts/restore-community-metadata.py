"""Restore metadata for existing recommendations; no ledger or Civitai mutations."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def restore(source,destination):
    destination=Path(destination).resolve();source=Path(source).resolve()
    if ROOT not in destination.parents:raise ValueError('Use the separate 3.0 workspace')
    os.environ['CIVITAI_HISTORY_DATA_DIR']=str(destination)
    from discovery.social import SocialClient
    manifest_path=destination/'sample.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    ledger=destination/'community'/'fixtures.sqlite3'
    with closing(sqlite3.connect(ledger.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        ids={r[0] for r in db.execute('SELECT image_id FROM recommendations')}
    history=(source/'history'/'history.sqlite3').resolve()
    before=(history.stat().st_size,history.stat().st_mtime_ns)
    result={'recommendations':len(ids),'urlsRestored':0,'tagInventoriesRestored':0,'unavailableUrls':0,'tagFailures':0}
    with closing(sqlite3.connect(history.as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.row_factory=sqlite3.Row
        for row in manifest['artworks']:
            if row['id'] not in ids:continue
            metadata=db.execute('SELECT username,url,width,height FROM images WHERE id=?',(row['id'],)).fetchone()
            if metadata and metadata['username'].casefold()==row['username'].casefold():
                if not row.get('sourcePreview'):result['urlsRestored']+=1
                row.update(sourcePreview=metadata['url'],width=metadata['width'],height=metadata['height'])
            if not row.get('sourcePreview'):
                try:
                    from discovery.taste import _image_url
                    import re
                    detail=SocialClient().query('image.get',{'id':row['id']})
                    owner=detail.get('user') or {}
                    if (detail.get('id')!=row['id'] or owner.get('id')!=row['creatorId']
                            or detail.get('availability')!='Public' or detail.get('ingestion')!='Scanned'
                            or detail.get('blockedFor') or detail.get('needsReview') is not None
                            or detail.get('nsfwLevel') not in (1,2,4,8,16)
                            or not re.fullmatch(r'[A-Za-z0-9_-]+',detail.get('url',''))):
                        raise ValueError('Public artwork metadata could not be confirmed')
                    row.update(sourcePreview=_image_url(detail['url'],detail.get('name'),512),
                        width=detail.get('width'),height=detail.get('height'),username=owner['username'],
                        browsingLevel=detail['nsfwLevel'],thumbnailProvenance='Civitai image.get public record')
                    result['urlsRestored']+=1
                except Exception:result['unavailableUrls']+=1
            if not row.get('tagsComplete'):
                try:
                    tags=SocialClient().query('tag.getVotableTags',{'id':row['id'],'type':'image'})
                    if not isinstance(tags,list) or any(not isinstance(t,dict) or not t.get('name') or type(t.get('id')) is not int for t in tags):
                        raise ValueError('Invalid upstream tag inventory')
                    row['tags']=[{'id':t['id'],'name':t['name']} for t in tags]
                    row['tagsComplete']=True;row['tagsProvenance']='Civitai tag.getVotableTags read'
                    result['tagInventoriesRestored']+=1
                except Exception:result['tagFailures']+=1
    temporary=manifest_path.with_suffix('.tmp');temporary.write_text(json.dumps(manifest,indent=2),encoding='utf-8');temporary.replace(manifest_path)
    result['sourceUnchanged']=before==(history.stat().st_size,history.stat().st_mtime_ns)
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True);args=parser.parse_args()
    restore(args.source,args.destination)

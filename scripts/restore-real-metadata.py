"""Add one completed real archive day to 3.0; never copy credentials or databases.

Source SQLite files are immutable/read-only. Destination rows are insert-only.
Existing days, identities, reactions and recommendations are never replaced.
"""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from discovery.history import HistoryArchive
from discovery.taste import TasteStore


def readonly(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');return db


def restore(source,destination,day):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if ROOT not in destination.parents or destination==source or source in destination.parents:
        raise ValueError('Destination must be the separate 3.0 workspace')
    source_files=[source/'history/history.sqlite3',source/'discovery/taste.sqlite3']
    before={str(path):(path.stat().st_size,path.stat().st_mtime_ns) for path in source_files}
    archive=HistoryArchive(destination/'history','X',(1,2,4,8,16))
    taste=TasteStore(destination/'discovery')
    with closing(readonly(source_files[1])) as origin,taste.connect() as target:
        source_account=origin.execute("SELECT value FROM sync_state WHERE key='account_id'").fetchone()
        target_account=target.execute("SELECT value FROM sync_state WHERE key='account_id'").fetchone()
        if not source_account or not target_account or source_account[0]!=target_account[0]:
            raise ValueError('Cached profile account must match the metadata source')
    with closing(readonly(source_files[0])) as origin,archive.connect() as target:
        target.execute('CREATE TABLE IF NOT EXISTS metadata_imports(day TEXT PRIMARY KEY,source_fingerprint TEXT,images INTEGER,imported_at TEXT)')
        prior=target.execute('SELECT * FROM metadata_imports WHERE day=?',(day,)).fetchone()
        fingerprint=hashlib.sha256(str(source).encode()).hexdigest()
        if target.execute('SELECT 1 FROM days WHERE day=?',(day,)).fetchone() and (not prior or prior['source_fingerprint']!=fingerprint):
            raise ValueError('Destination already has this day without matching provenance; refusing to replace it')
        complete=origin.execute('SELECT * FROM days WHERE day=? AND complete=1',(day,)).fetchone()
        if not complete:raise ValueError('Source day is not complete')
        rows=origin.execute('SELECT i.* FROM block_images b JOIN images i ON i.id=b.image_id WHERE b.block_key=?',(day,)).fetchall()
        if not rows or any('/community-fixture.svg' in r['url'] for r in rows):
            raise ValueError('Source does not contain a complete real artwork day')
        source_rows={row['id']:row for row in rows};image_ids=list(source_rows)
        for start in range(0,len(image_ids),500):
            chunk=image_ids[start:start+500];holes=','.join('?' for _ in chunk)
            for existing in target.execute(f'SELECT id,username_key,url FROM images WHERE id IN ({holes})',chunk):
                original=source_rows[existing['id']]
                if existing['username_key']!=original['username_key'] or existing['url']!=original['url']:
                    raise ValueError('Conflicting existing artwork ID; no records were replaced')
        if prior:
            existing_ids={r[0] for r in target.execute('SELECT image_id FROM block_images WHERE block_key=?',(day,))}
            if existing_ids!=set(image_ids) or prior['images']!=len(rows):
                raise ValueError('Existing imported day differs from source; refusing to repair silently')
        images_before=target.execute('SELECT count(*) FROM images').fetchone()[0]
        # Public metadata only. Prompt/details are fetched through the normal viewer.
        names=['id','local_date','post_id','username','username_key','created_at','url','width','height','type',
               'nsfw_level','browsing_level','base_model','model_version_ids','stats','visual_hash']
        target.executemany(f"INSERT OR IGNORE INTO images({','.join(names)}) VALUES({','.join('?' for _ in names)})",
            [tuple(row[name] for name in names) for row in rows])
        keys=[day,day+'#morning',day+'#evening']
        for key in keys:
            record=origin.execute('SELECT * FROM days WHERE day=? AND complete=1',(key,)).fetchone()
            if not record:continue
            columns=[r[1] for r in target.execute('PRAGMA table_info(days)') if r[1] in record.keys()]
            target.execute(f"INSERT OR IGNORE INTO days({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",tuple(record[name] for name in columns))
            target.executemany('INSERT OR IGNORE INTO block_images VALUES(?,?)',
                [(key,r[0]) for r in origin.execute('SELECT image_id FROM block_images WHERE block_key=?',(key,))])
        stamp=datetime.now(timezone.utc).isoformat()
        target.execute('INSERT OR IGNORE INTO metadata_imports VALUES(?,?,?,?)',(day,hashlib.sha256(str(source).encode()).hexdigest(),len(rows),stamp))
        images_added=target.execute('SELECT count(*) FROM images').fetchone()[0]-images_before
    imported_ids=[r['id'] for r in rows]
    tags=0;known=0
    with closing(readonly(source_files[1])) as origin,taste.connect() as target:
        for start in range(0,len(imported_ids),500):
            ids=imported_ids[start:start+500];holes=','.join('?' for _ in ids)
            snapshots=origin.execute(f'SELECT image_id,fetched_at FROM archive_image_seen WHERE image_id IN ({holes})',ids).fetchall()
            # A seen marker declares a complete inventory. Never merge into or
            # refresh an existing snapshot from a historical import.
            existing={r[0] for r in target.execute(f'SELECT image_id FROM archive_image_seen WHERE image_id IN ({holes})',ids)}
            missing=[r for r in snapshots if r['image_id'] not in existing]
            for snapshot in missing:
                inventory=origin.execute('SELECT image_id,tag_name FROM archive_image_tags WHERE image_id=?',(snapshot['image_id'],)).fetchall()
                partial={r[0] for r in target.execute('SELECT tag_name FROM archive_image_tags WHERE image_id=?',(snapshot['image_id'],))}
                if not partial <= {r['tag_name'] for r in inventory}:
                    raise ValueError('Conflicting incomplete tag inventory; existing metadata was preserved')
                target.executemany('INSERT OR IGNORE INTO archive_image_tags(image_id,tag_name) VALUES(?,?)',inventory)
                target.execute('INSERT INTO archive_image_seen(image_id,fetched_at) VALUES(?,?)',tuple(snapshot))
                known+=1;tags+=len(inventory)
    for key in keys:
        with archive.connect() as db:exists=db.execute('SELECT 1 FROM days WHERE day=?',(key,)).fetchone()
        if exists:archive.build_artist_index(key)
    unchanged=all((path.stat().st_size,path.stat().st_mtime_ns)==before[str(path)] for path in source_files)
    report={'day':day,'realImages':len(rows),'tagSnapshots':known,'tags':tags,'sourceFilesUnchanged':unchanged,
        'imagesAdded':images_added,'alreadyImported':bool(prior),'profileAccountMatched':True,'destination':str(destination),'existingRowsReplaced':0}
    with archive.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata_import_runs(run_id INTEGER PRIMARY KEY,day TEXT NOT NULL,source_fingerprint TEXT NOT NULL,completed_at TEXT NOT NULL,images_added INTEGER NOT NULL,tag_snapshots_added INTEGER NOT NULL,tags_added INTEGER NOT NULL,source_files TEXT NOT NULL)')
        db.execute('INSERT INTO metadata_import_runs(day,source_fingerprint,completed_at,images_added,tag_snapshots_added,tags_added,source_files) VALUES(?,?,?,?,?,?,?)',
            (day,fingerprint,datetime.now(timezone.utc).isoformat(),images_added,known,tags,json.dumps(before)))
    print(json.dumps(report,indent=2));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True);parser.add_argument('--day',required=True)
    args=parser.parse_args();restore(args.source,args.destination,args.day)

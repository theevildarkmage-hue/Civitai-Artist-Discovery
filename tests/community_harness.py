"""Managed, challenged 3.0 fixture server; never connects to an existing app."""
from __future__ import annotations
import test_safety
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import tempfile
from test_safety import IsolatedEndpoint, ROOT


class Harness:
    def __init__(self, *, port=8907, root=None, sample=True):
        if root is None:
            self.temporary=tempfile.TemporaryDirectory(prefix='data-test-community-run-',ignore_cleanup_errors=True)
            root=self.temporary.name
            if sample:
                # Fully synthetic manifest: clean worktrees never read personal caches.
                sys.path.insert(0,str(ROOT))
                from discovery.community import FixtureVerifier
                verifier=FixtureVerifier(development=True)
                artworks=[{'id':art.id,'creatorId':art.creator_id,'username':art.username,
                    'browsingLevel':art.rating,'tags':[{'id':i,'name':name}
                        for i,name in zip(art.tag_ids,art.tags)],'tagsComplete':True}
                    for art in verifier.artworks.values() if art.id!=1099]
                manifest={'developmentOnly':True,'schemaVersion':1,'artworks':artworks,
                    'sourceStats':{'synthetic':True,'artworks':len(artworks)},
                    'negativeCases':[{'id':2001+i,'state':state} for i,state in enumerate(
                        ('missing_creator','unverified_owner','deleted','inaccessible','incomplete'))]}
                Path(root,'sample.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.port=port; self.instance=secrets.token_hex(24); self.process=None
        self.sample=sample
        self.env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':str(self.root),'CAD_COMMUNITY_MODE':'fixture',
            'CAD_DEVELOPMENT':'1','CAD_COMMUNITY_TESTING':'1','CAD_TEST_INSTANCE':self.instance}
        if sample: self.env['CAD_COMMUNITY_SAMPLE']=str(self.root/'sample.json')
        else: self.env.pop('CAD_COMMUNITY_SAMPLE',None)
        self.base=f'http://127.0.0.1:{port}'

    def start(self):
        if (self.root/'oauth_tokens.dpapi').exists() or (self.root/'oauth_client.json').exists():
            raise PermissionError('Simulated-user tests refuse a data root containing OAuth credentials/configuration')
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',self.port))==0:
                raise PermissionError('Required isolated test port is occupied; refusing to contact or stop its owner')
        settings=self.root/'settings.json'
        if not settings.exists():
            settings.write_text(json.dumps({'browsingLevels':[1,2,4,8,16], 'autoCapture':False,'checkForUpdates':False}))
        logs=test_safety.REPORTS;logs.mkdir(exist_ok=True)
        self.log=open(logs/'community-stress-server.log','a',encoding='utf-8')
        self.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--host','127.0.0.1',
            '--port',str(self.port),'--no-browser'],cwd=ROOT,env=self.env,stdout=self.log,stderr=self.log)
        self.endpoint=IsolatedEndpoint(self.base,self.instance,self.root)
        for _ in range(100):
            if self.process.poll() is not None:raise RuntimeError('Isolated test server exited before readiness')
            try:self.endpoint.verify();return self
            except OSError:time.sleep(.1)
        raise RuntimeError('Isolated test server failed readiness')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=10)
        if hasattr(self,'log'):self.log.close()

    def control(self, action, **fields):
        return self.endpoint.request('/api/community/development/control',{'action':action,**fields})

    def snapshot(self):return self.control('snapshot')

    def reset(self):
        self.control('reset')
        self.endpoint.request('/api/settings',{'browsingLevels':[1,2,4,8,16]})

    def seed_personal(self):
        # This imports the new clone's code and creates only this test root's archive.
        os.environ['CIVITAI_HISTORY_DATA_DIR']=str(self.root)
        sys.path.insert(0,str(ROOT))
        from discovery.history import HistoryArchive
        history=HistoryArchive(self.root/'history','X',(1,2,4,8,16))
        self.day=(datetime.now()-timedelta(days=1)).date().isoformat()
        items=[{'id':600000+i,'postId':600000+i,'username':f'PersonalFixture{i:02d}',
            'createdAt':f'{self.day}T13:00:00Z','url':f'{self.base}/community-fixture.svg?id={600000+i}',
            'width':640,'height':800,'type':'image','browsingLevel':1,'nsfwLevel':'None',
            'stats':{'reactionCount':i}} for i in range(1,13)]
        history._upsert_normalized(items,forced_date=self.day)
        with history.connect() as db:
            db.execute('INSERT OR REPLACE INTO days(day,complete,updated_at,content_rating,collection_version) VALUES(?,1,?,?,5)',
                (self.day,datetime.now().isoformat(),'X'))
        history.build_artist_index(self.day)
        from discovery.taste import TasteStore
        taste= TasteStore(self.root/'discovery')
        with taste.connect() as db:
            db.executemany('INSERT OR REPLACE INTO archive_image_seen(image_id,fetched_at) VALUES(?,?)',
                [(row['id'],datetime.now().isoformat()) for row in items])

    def headers(self):return {'X-CAD-Test-Instance':self.instance}

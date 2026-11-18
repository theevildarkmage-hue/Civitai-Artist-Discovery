"""Actual local HTTP/SQLite filtering; synthetic metadata and provider mirror only."""
import test_safety
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from itertools import product
from pathlib import Path
from unittest.mock import patch
import json,tempfile,threading,urllib.request
import server
from discovery.history import HistoryArchive
from discovery.timemachine import TimeMachine
from discovery.taste import TasteStore
from discovery.settings import AppSettings

with ExitStack() as stack:
    root=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='cad3-tm-filters-')))
    history=HistoryArchive(root/'history');taste=TasteStore(root/'taste')
    machine=TimeMachine(root/'machine',history,taste);settings=AppSettings(root/'settings.json')
    class Provider:
        def query(self,*args):
            return {'hiddenUsers':[{'id':3,'username':'Blocked'}], 'blockedUsers':[], 'blockedByUsers':[],
                    'hiddenTags':[{'id':2,'name':'hidden-topic'}], 'hiddenImages':[4], 'hiddenImagesImplicit':[]}
    with patch('discovery.taste.auth_status',return_value={'id':42}):taste.import_hidden_preferences(Provider())
    with taste.connect() as db:
        db.execute("INSERT INTO archive_image_tags VALUES(2,'hidden-topic')")
        db.executemany('INSERT INTO archive_image_seen VALUES(?,?)',[(i,'2026-11-18T00:00:00Z') for i in range(1,6)])
    with machine.connect() as db:
        for name in ('Allowed','Blocked'):
            db.execute('INSERT INTO creator_progress(username_key,username,exhausted,updated_at) VALUES(?,?,1,?)',(name.lower(),name,'2026-11-18'))
        for i in range(1,6):
            name='Blocked' if i==3 else 'Allowed'
            db.execute('INSERT INTO creator_images(username_key,username,position,image_id,created_at,url,browsing_level) VALUES(?,?,?,?,?,?,?)',
                       (name.lower(),name,i,i,'2026-11-17T12:00:00Z',f'https://image.civitai.com/test/{i}.jpg',4 if i==5 else 1))
    def snapshot():
        with machine.connect() as db:return {t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY 1,2')] for t in ('creator_images','creator_progress')}
    before=snapshot()
    for name,value in [('HISTORY',history),('TASTE',taste),('TIME_MACHINE',machine),('SETTINGS',settings)]:
        stack.enter_context(patch.object(server,name,value))
    stack.enter_context(patch.object(taste,'ensure_hidden_preferences_current',return_value=None))
    stack.enter_context(patch.object(server,'creator_profiles',return_value={}))
    stack.enter_context(patch.object(server,'gallery_signals',return_value={'followed':set()}))
    stack.enter_context(patch.object(server,'auth_status',return_value={'id':42}))
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    stack.enter_context(patch.object(server,'RUNNING_PORT',http.server_port))
    threading.Thread(target=http.serve_forever,daemon=True).start()
    stack.callback(http.server_close);stack.callback(http.shutdown)
    def get(path):
        with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}'+path) as response:return json.load(response)
    for tag,account,image,mature in product((True,False),repeat=4):
        levels=[1,2,4] if mature else [1,2]
        settings.update(browsing_levels_value=levels,apply_tag_filters_value=tag,apply_account_filters_value=account,apply_image_filters_value=image)
        history.set_content_filter(levels)
        expected={1,2,4,5}
        if tag:expected.discard(2)
        if image:expected.discard(4)
        if not mature:expected.discard(5)
        data=get('/api/timemachine/creator?username=Allowed&savedOnly=1')
        assert {r['id'] for r in data['images']}==expected,(tag,account,image,mature,data)
        assert {r['representative']['id'] for r in data['cards']}==expected
        blocked=get('/api/timemachine/creator?username=Blocked&savedOnly=1')
        assert bool(blocked['images']) is not account
        walk=get('/api/timemachine')['cards']
        assert {r['representative']['id'] for r in walk}==({1} if account else {1,3})
    assert snapshot()==before,'Filtering must not advance or rewrite saved positions/images'
print({'httpSettingCombinations':16,'creatorHistoryAndGrid':True,'progressAndImagesPreserved':True,'realProviderUsed':False})

"""Read-only diagnostics, credential suppression and production/fixture boundaries."""
import test_safety
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
sys.path.insert(0,str(test_safety.ROOT))
from discovery import dev_diagnostics as diagnostics
from discovery.cache import MetadataCache
from discovery.community import FixtureVerifier,LocalCommunityBackend


class DevelopmentDiagnostics(unittest.TestCase):
    def test_snapshot_does_not_write_or_expose_credentials(self):
        with tempfile.TemporaryDirectory(prefix='cad3-diagnostics-') as temporary:
            root=Path(temporary);(root/'discovery').mkdir();(root/'history').mkdir()
            with sqlite3.connect(root/'discovery/taste.sqlite3') as db:
                db.executescript('''CREATE TABLE reacted_images(id INTEGER);INSERT INTO reacted_images VALUES(1);
                    CREATE TABLE reacted_reactions(id INTEGER);INSERT INTO reacted_reactions VALUES(1);
                    CREATE TABLE followed_creators(id INTEGER);CREATE TABLE hidden_creators(creator_id INTEGER,username_key TEXT,reason TEXT);
                    CREATE TABLE hidden_images(image_id INTEGER);CREATE TABLE hidden_tags(tag_id INTEGER,tag_name TEXT);
                    CREATE TABLE sync_state(key TEXT,value TEXT);INSERT INTO sync_state VALUES('last_sync_at','2026-10-15');''')
            db.close()
            with sqlite3.connect(root/'history/history.sqlite3') as db:
                db.executescript("""CREATE TABLE images(id INTEGER,url TEXT);
                    INSERT INTO images VALUES(1,'https://image.civitai.com/real.jpeg');
                    INSERT INTO images VALUES(2,'/community-fixture.svg?id=2');
                    CREATE TABLE days(day TEXT,complete INTEGER);
                    INSERT INTO days VALUES('2026-10-14',1),('2026-10-14#morning',1);
                    CREATE TABLE block_images(block_key TEXT,image_id INTEGER);
                    INSERT INTO block_images VALUES('2026-10-14',1),('2026-10-14#morning',2);""")
            db.close()
            cache=MetadataCache(root/'cache/community-metadata.sqlite3')
            verifier=FixtureVerifier(development=True)
            backend=LocalCommunityBackend(root/'community/fixtures.sqlite3',verifier)
            for image in (1001,1006,1011):
                backend.request('recommend','fixture:1',{'artwork':image})
                cache.put(f'fixture:artwork:{image}',asdict(verifier.artworks[image]))
            (root/'settings.json').write_text(json.dumps({'browsingLevels':[1,2,4,8,16]}))
            (root/'error.log').write_text('Authorization: Bearer sentinel-private\nrefresh_token=sentinel-refresh')
            def digest():
                return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith(('-shm','-wal'))}
            before=digest()
            with patch.dict(os.environ,{'CAD_COMMUNITY_MODE':'fixture'}),patch.object(diagnostics,'connectivity',return_value={'fixture':{'state':'reachable'}}) as probe:
                value=diagnostics.snapshot(test_safety.ROOT,root,'3.0.0-dev','commit',
                    auth_status=lambda:{'connected':True,'access_token':'sentinel-private'},job={'running':False},probe=True)
            self.assertEqual(value['community']['recommendations'],3)
            self.assertEqual(value['community']['cachedContentControls']['visible'],3)
            self.assertEqual(value['dailyGallery']['artworkRows'],1)
            self.assertEqual(value['dailyGallery']['latestCompleted'],'2026-10-14')
            self.assertEqual(value['profile']['reacted_images'],1)
            self.assertNotIn('sentinel-',json.dumps(value));probe.assert_called_once()
            self.assertEqual(before,digest())

    def test_packaged_build_cannot_enable_diagnostics(self):
        with patch.dict(os.environ,{'CAD_DEVELOPMENT':'1'}),patch.object(sys,'frozen',True,create=True):
            self.assertFalse(diagnostics.enabled())

    def test_http_boundary_production_and_unchallenged_fixture(self):
        for testing in (False,True):
            with tempfile.TemporaryDirectory(prefix='data-test-diagnostics-http-') as temporary:
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                env={**os.environ,'CIVITAI_HISTORY_DATA_DIR':temporary,'CAD_DEVELOPMENT':'1' if testing else '0',
                    'CAD_COMMUNITY_TESTING':'1' if testing else '0','CAD_COMMUNITY_MODE':'fixture' if testing else 'offline',
                    'CAD_TEST_INSTANCE':'x'*48}
                Path(temporary,'settings.json').write_text('{"autoCapture":false,"checkForUpdates":false}')
                process=subprocess.Popen([sys.executable,str(test_safety.ROOT/'server.py'),'--port',str(port),'--no-browser'],env=env,cwd=test_safety.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                base=f'http://127.0.0.1:{port}'
                try:
                    for _ in range(100):
                        if process.poll() is not None:self.fail('Isolated server exited')
                        try:urllib.request.urlopen(base+'/api/history/config',timeout=1).close();break
                        except OSError:time.sleep(.05)
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(base+'/api/development/diagnostics',timeout=3)
                    self.assertEqual(denied.exception.code,403 if testing else 404)
                    if testing:
                        request=urllib.request.Request(base+'/api/development/diagnostics',headers={'X-CAD-Test-Instance':'x'*48})
                        with urllib.request.urlopen(request,timeout=3) as response:value=json.load(response)
                        self.assertTrue(value['readOnly']);self.assertFalse(value['authentication']['connected'])
                finally:process.terminate();process.wait(timeout=10)


if __name__=='__main__':unittest.main()

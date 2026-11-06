"""Conservative stable upgrades preserve preferences and ambiguous source bytes."""
import test_safety
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import sys
import os
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import settings
from discovery.site import levels_for_rating

with tempfile.TemporaryDirectory(prefix='cad3-capture-upgrade-') as directory:
    base=Path(directory)
    def profile(name):
        root=base/name;root.mkdir()
        return root/'settings.json'
    for channel in ('beta','stable'):
        with patch.object(settings,'CHANNEL',channel):
            for rating in ('Soft','Mature','X'):
                for enabled in (False,True):
                    path=profile(f'{channel}-{rating}-{enabled}')
                    raw={'contentRating':rating,'browsingLevels':list(levels_for_rating(rating)),
                         'autoCapture':enabled,'captureCoverage':rating,'autoCaptureHours':24,
                         'autoCaptureMinute':60,'captureSeed':199,'checkForUpdates':False,
                         'hideHighVolumeCreators':False,'highVolumeThreshold':150}
                    path.write_text(json.dumps(raw));original=path.read_bytes()
                    value=settings.AppSettings(path).load()
                    for name in raw:assert value[name]==raw[name],(channel,name)
                    assert path.read_bytes()==original
                path=profile(f'{channel}-{rating}-sparse')
                raw={'contentRating':rating,'browsingLevels':list(levels_for_rating(rating))}
                path.write_text(json.dumps(raw));original=path.read_bytes()
                value=settings.AppSettings(path).load()
                assert value['captureCoverage']==rating
                assert value['autoCapture'] is False
                assert value['browsingLevels']==raw['browsingLevels']
                assert path.read_bytes()==original
                path.write_text(json.dumps({**raw,'autoCapture':False}))
                assert settings.AppSettings(path).load()['autoCapture'] is False
            # A genuinely empty installation retains the approved first-run defaults.
            path=profile(f'{channel}-fresh')
            app=settings.AppSettings(path)
            assert app.load()['autoCapture'] is True and app.load()['captureCoverage']=='X'
            saved=app.update(check_for_updates_value=False)
            assert settings.AppSettings(path).load()==saved
            # Missing settings alongside existing cached data is a stable upgrade.
            path=profile(f'{channel}-missing')
            history=path.parent/'history';history.mkdir()
            (history/'day.sqlite').write_bytes(b'disposable existing profile marker')
            app=settings.AppSettings(path)
            value=app.load()
            assert value['autoCapture'] is False
            assert value['captureCoverage']=='Soft'
            assert not path.exists()
            saved=app.update(dim_seen_cards_value=False)
            assert settings.AppSettings(path).load()==saved
            assert (history/'day.sqlite').read_bytes()==b'disposable existing profile marker'
            for name,original in [('empty',b'{}'),('zero',b''),('malformed',b'{"contentRating":'),
                                  ('null',b'null'),('array',b'[]'),('scalar',b'42'),('utf8',b'\xff')]:
                path=profile(f'{channel}-{name}');path.write_bytes(original)
                app=settings.AppSettings(path)
                value=app.load()
                assert value['autoCapture'] is False and value['captureCoverage']=='Soft'
                assert value['browsingLevels']==[1,2]
                assert path.read_bytes()==original and not list(path.parent.glob('*.bak'))
                # A failed dependent operation restores the exact original bytes.
                def fail(_):raise RuntimeError('controlled dependent failure')
                try:app.transactional_update(effect=fail,dim_seen_cards_value=False)
                except RuntimeError:pass
                else:raise AssertionError('rollback failed to propagate error')
                assert path.read_bytes()==original
                assert path.with_name('settings.json.invalid.bak').read_bytes()==original
                saved=app.update(dim_seen_cards_value=False)
                assert saved['autoCapture'] is False and saved['captureCoverage']=='Soft'
                assert settings.AppSettings(path).load()==saved
                assert path.with_name('settings.json.invalid.bak').read_bytes()==original
    with patch.object(settings,'CHANNEL','stable'):
        for name,raw,coverage in [
            ('levels-only',{'browsingLevels':[4]},'Mature'),
            ('conflict',{'contentRating':'Soft','browsingLevels':[16]},'Soft'),
            ('invalid-fields',{'autoCapture':'yes','captureCoverage':'invalid'},'Soft')]:
            path=profile(name);path.write_text(json.dumps(raw))
            saved=settings.AppSettings(path).update(dim_seen_cards_value=False)
            assert saved['autoCapture'] is False and saved['captureCoverage']==coverage
            assert settings.AppSettings(path).load()==saved
        # Transaction rollback also preserves an absent file and valid explicit data.
        for name,original in [('absent',None),('explicit',b'{"autoCapture":true,"captureCoverage":"Mature"}')]:
            path=profile('rollback-'+name)
            if original is not None:path.write_bytes(original)
            app=settings.AppSettings(path)
            try:app.transactional_update(effect=fail,auto_capture_value=False)
            except RuntimeError:pass
            else:raise AssertionError('expected failure')
            assert (path.read_bytes() if path.exists() else None)==original
        # A later distinct damaged file never overwrites earlier recovery evidence.
        path=profile('preserve-multiple');path.write_bytes(b'{broken')
        settings.AppSettings(path).update(dim_seen_cards_value=False)
        path.write_bytes(b'[other damage')
        settings.AppSettings(path).update(dim_seen_cards_value=False)
        assert {p.read_bytes() for p in path.parent.glob('*.bak')}=={b'{broken',b'[other damage'}
    # Exercise the real module initialization order: thumbnail/cache directories
    # created before AppSettings must not turn an empty install into an upgrade.
    fresh=base/'cad3-server-fresh'
    environment=dict(os.environ, CIVITAI_HISTORY_DATA_DIR=str(fresh))
    probe=subprocess.run([sys.executable,'-c',
        "import sys;sys.path.insert(0,'tests');import test_safety;import server;"
        "assert server.INITIAL_SETTINGS['autoCapture'] is True;"
        "assert server.INITIAL_SETTINGS['captureCoverage']=='X'"],
        cwd=Path(__file__).resolve().parents[1],env=environment,
        capture_output=True,text=True,timeout=30)
    assert probe.returncode==0,(probe.stdout,probe.stderr)
print('PASS: fresh defaults; explicit/sparse/missing/empty/malformed/invalid-UTF8 safety; restart persistence; exact-byte rollback and retained originals')

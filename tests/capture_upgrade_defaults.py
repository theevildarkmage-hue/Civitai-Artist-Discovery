"""Stable legacy capture inheritance without changing beta or new-install policy."""
import test_safety
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from discovery import settings
from discovery.site import levels_for_rating

with tempfile.TemporaryDirectory(prefix='cad3-capture-upgrade-') as directory:
    base=Path(directory)
    for channel in ('beta','stable'):
        with patch.object(settings,'CHANNEL',channel):
            for rating in ('Soft','Mature','X'):
                for enabled in (False,True):
                    path=base/f'{channel}-{rating}-{enabled}.json'
                    # Realistic full stable preferences with explicit capture choices.
                    raw={'contentRating':rating,'browsingLevels':list(levels_for_rating(rating)),
                         'autoCapture':enabled,'captureCoverage':rating,'autoCaptureHours':24,
                         'autoCaptureMinute':60,'captureSeed':199,'checkForUpdates':False,
                         'hideHighVolumeCreators':False,'highVolumeThreshold':150}
                    path.write_text(json.dumps(raw));original=path.read_bytes()
                    value=settings.AppSettings(path).load()
                    assert value['autoCapture'] is enabled and value['captureCoverage']==rating
                    for name in raw:assert value[name]==raw[name],(channel,name)
                    assert path.read_bytes()==original
                path=base/f'{channel}-{rating}-sparse.json'
                raw={'contentRating':rating,'browsingLevels':list(levels_for_rating(rating))}
                path.write_text(json.dumps(raw));original=path.read_bytes()
                value=settings.AppSettings(path).load()
                assert value['captureCoverage']==(rating if channel=='stable' else 'X')
                assert value['autoCapture'] is (channel=='beta')
                assert value['browsingLevels']==raw['browsingLevels']
                assert path.read_bytes()==original
                # An explicitly disabled collector stays off even without saved coverage.
                path.write_text(json.dumps({**raw,'autoCapture':False}))
                assert settings.AppSettings(path).load()['autoCapture'] is False
            for name,text in [('new',None),('empty','{}'),('malformed','{"contentRating":'),
                              ('null','null'),('array','[]'),('scalar','42')]:
                path=base/f'{channel}-{name}.json'
                if text is not None:path.write_text(text)
                original=path.read_bytes() if path.exists() else None
                value=settings.AppSettings(path).load()
                # Empty/malformed profiles retain the approved current defaults;
                # this task makes no new-install or ambiguous-profile policy choice.
                assert value['autoCapture'] is True and value['captureCoverage']=='X'
                assert value['browsingLevels']==[1,2]
                assert (path.read_bytes() if path.exists() else None)==original
    # A stable sparse preference change persists its inherited capture settings.
    path=base/'stable-save.json';path.write_text('{"contentRating":"Soft"}')
    with patch.object(settings,'CHANNEL','stable'):
        value=settings.AppSettings(path).update(dim_seen_cards_value=False)
        assert value['captureCoverage']=='Soft' and value['autoCapture'] is False
        assert json.loads(path.read_text())['captureCoverage']=='Soft'
        assert settings.AppSettings(path).load()['captureCoverage']=='Soft'
print('PASS: explicit capture choices, stable sparse inheritance, beta/new/empty/malformed defaults, non-mutating load and persisted upgrade')

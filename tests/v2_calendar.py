"""Calendar coverage combines full and partial archive blocks without hydrating cards."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='v2-calendar-', ignore_cleanup_errors=True) as temporary:
    os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
    from discovery.history import HistoryArchive

    history = HistoryArchive(Path(temporary) / 'history', 'Soft')
    with history.connect() as db:
        db.executemany('INSERT INTO days(day,complete) VALUES(?,?)', [
            ('2026-09-01', 1), ('2026-09-02#morning', 1),
            ('2026-09-02#evening', 0), ('2026-09-03#evening', 1),
        ])
    assert history.calendar_days() == [
        {'date': '2026-09-01', 'all': True, 'morning': False, 'evening': False},
        {'date': '2026-09-02', 'all': False, 'morning': True, 'evening': False},
        {'date': '2026-09-03', 'all': False, 'morning': False, 'evening': True},
    ]
    print({'fullDay': True, 'partialBlocks': True, 'noArtworkHydration': True})

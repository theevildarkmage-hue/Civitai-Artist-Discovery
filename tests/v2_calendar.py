"""Calendar coverage combines full and partial archive blocks without hydrating cards."""

import test_safety  # Fail closed before any test network request.
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
    history._upsert_normalized([{'id': 991, 'username': 'Synthetic',
        'createdAt': '2026-09-03T12:00:00Z', 'url': '/community-fixture.svg?id=991'}],
        forced_date='2026-09-03#evening')
    # Refuse every artwork-table read: even fixture exclusion must stay metadata-only.
    original_connect = history.connect
    from contextlib import contextmanager
    @contextmanager
    def bounded_connect():
        with original_connect() as db:
            import sqlite3
            db.set_authorizer(lambda action, table, *args:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'images'
                else sqlite3.SQLITE_OK)
            yield db
    history.connect = bounded_connect
    assert [day['date'] for day in history.calendar_days(exclude_fixtures=True)] == ['2026-09-01', '2026-09-02']
    history.connect = original_connect
    # A rejected non-archive insert cannot replace or quarantine an existing real image.
    real = {'id': 992, 'username': 'Real', 'createdAt': '2026-09-01T12:00:00Z',
            'url': 'https://image.civitai.com/real.jpg'}
    history._upsert_normalized([real], forced_date='2026-09-01')
    history._upsert_normalized([{**real, 'url': '/community-fixture.svg?id=992'}], archive=False)
    with history.connect() as db:
        assert db.execute('SELECT image_id FROM fixture_archive_images WHERE image_id=992').fetchone() is None
    # Replacing synthetic metadata with real metadata removes the quarantine marker.
    history._upsert_normalized([{**real, 'id': 991}], forced_date='2026-09-03#evening')
    assert len(history.calendar_days(exclude_fixtures=True)) == 3
    print({'fullDay': True, 'partialBlocks': True, 'noArtworkHydration': True})

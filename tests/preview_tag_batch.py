"""Cached preview decisions preserve safety and read a whole page in one connection."""
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix='preview-tag-batch-', ignore_cleanup_errors=True) as temporary:
    os.environ['CIVITAI_HISTORY_DATA_DIR'] = temporary
    from discovery.taste import TasteStore

    store = TasteStore(Path(temporary) / 'discovery')
    with store.connect() as db:
        db.executemany('INSERT INTO archive_image_tags(image_id,tag_name) VALUES(?,?)',
                       [(i, 'landscape') for i in range(1, 51)])
        db.execute("INSERT INTO archive_image_tags(image_id,tag_name) VALUES(1,'blocked')")
        db.execute("INSERT INTO hidden_tags(tag_id,tag_name) VALUES(1,'blocked')")
        db.execute("INSERT INTO archive_image_seen(image_id,fetched_at) VALUES(51,'now')")
    original_connect = store.connect
    with store.connect() as db:
        query = ('SELECT DISTINCT image_id FROM archive_image_tags '
                 'WHERE tag_name IN (SELECT tag_name FROM hidden_tags)')
        plan = ' '.join(str(tuple(row)) for row in db.execute('EXPLAIN QUERY PLAN ' + query))
        assert 'SEARCH archive_image_tags USING COVERING INDEX archive_tags_name_image' in plan, plan
        previous = {row[0] for row in db.execute(
            'SELECT DISTINCT t.image_id FROM archive_image_tags t '
            'JOIN hidden_tags h ON h.tag_name=t.tag_name')}
        assert store.hidden_image_ids() == previous == {1}
    connections = []

    def counted_connect():
        connections.append(1)
        return original_connect()

    store.connect = counted_connect
    values = store.image_tags_many([*range(1, 805), 1])
    assert len(connections) == 1, len(connections)
    assert len(values) == 804
    assert values[1] == {'known': True, 'tags': [
        {'name': 'blocked', 'hidden': True}, {'name': 'landscape', 'hidden': False}]}
    assert values[51] == {'known': True, 'tags': []}
    assert values[52] == {'known': False, 'tags': []}
    with store.connect() as db:
        db.execute('DELETE FROM hidden_tags')
    assert not any(tag['hidden'] for tag in store.image_tags_many([1])[1]['tags'])
    assert store.image_tags_many([]) == {}

    # Compare the former endpoint's per-image access pattern against the batch on the
    # same synthetic store. Report timings; only query equivalence is a pass/fail gate.
    timings = {'perImageMs': [], 'batchMs': []}
    for _ in range(5):
        start = time.perf_counter()
        old = {i: store.image_tags(i) for i in range(1, 51)}
        timings['perImageMs'].append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        batch = store.image_tags_many(range(1, 51))
        timings['batchMs'].append((time.perf_counter() - start) * 1000)
        assert batch == old
    print({'singleConnection': True, 'unknownPreserved': True, 'hiddenSnapshotRefreshes': True,
           'median50Images': {key: round(statistics.median(value), 2) for key, value in timings.items()}})

"""Compare hidden-tag queries on a disposable SQLite snapshot; never alters the source."""
import argparse
from pathlib import Path
import sqlite3
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('database', type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='civitai-tag-timing-') as temporary:
    source = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)
    copy = sqlite3.connect(str(Path(temporary) / 'snapshot.sqlite3'))
    try:
        source.backup(copy)
        # Match the v1 schema in the disposable copy only.
        copy.execute('DROP INDEX IF EXISTS archive_tags_name_image')
        before_query = ('SELECT DISTINCT t.image_id FROM archive_image_tags t '
                        'JOIN hidden_tags h ON h.tag_name=t.tag_name')
        start = time.perf_counter()
        before = {row[0] for row in copy.execute(before_query)}
        before_ms = (time.perf_counter() - start) * 1000
        copy.execute('CREATE INDEX archive_tags_name_image ON archive_image_tags(tag_name,image_id)')
        start = time.perf_counter()
        after = {row[0] for row in copy.execute('SELECT DISTINCT image_id FROM archive_image_tags '
                                               'WHERE tag_name IN (SELECT tag_name FROM hidden_tags)')}
        after_ms = (time.perf_counter() - start) * 1000
        assert before == after, 'Hidden-image decisions changed'
        print({'beforeMs': round(before_ms, 2), 'afterMs': round(after_ms, 2),
               'identicalDecisions': True, 'matchedImages': len(after)})
    finally:
        source.close()
        copy.close()

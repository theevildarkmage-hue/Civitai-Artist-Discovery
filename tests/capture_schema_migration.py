"""Atomic additive retry schema, existing archive preservation and conflicting tables."""
import test_safety
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(test_safety.ROOT))
from discovery.history import HistoryArchive


class CaptureSchemaMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-capture-schema-')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'history.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE preserved(id INTEGER PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO preserved VALUES(1,'existing archive sentinel')")
            db.execute('PRAGMA user_version=37')
        db.close()
        self.archive=HistoryArchive.__new__(HistoryArchive)
        self.archive.db_path=self.path

    def test_additive_and_repeatable(self):
        self.archive._ensure_capture_recovery_schema()
        with self.archive.connect() as db:
            db.execute("INSERT INTO capture_recovery VALUES('2026-10-14#morning','interrupted',2,0,123)")
        self.archive._ensure_capture_recovery_schema()
        with self.archive.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],37)
            self.assertEqual(tuple(db.execute('SELECT * FROM preserved').fetchone()),(1,'existing archive sentinel'))
            self.assertEqual(db.execute('SELECT attempts FROM capture_recovery').fetchone()[0],2)

    def test_incompatible_table_is_preserved(self):
        with self.archive.connect() as db:
            db.execute('CREATE TABLE capture_recovery(legacy TEXT)')
            db.execute("INSERT INTO capture_recovery VALUES('do not replace')")
        with self.assertRaisesRegex(RuntimeError,'Incompatible'):
            self.archive._ensure_capture_recovery_schema()
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT legacy FROM capture_recovery').fetchone()[0],'do not replace')
            self.assertEqual(db.execute('SELECT count(*) FROM preserved').fetchone()[0],1)

    def test_concurrent_migration(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _:self.archive._ensure_capture_recovery_schema(),range(6)))
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM capture_recovery').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')


if __name__=='__main__':unittest.main()

"""Once-only legacy membership migration, atomic rollback and scan-free adoption."""
import test_safety
import sqlite3
import tempfile
from pathlib import Path
import unittest
from discovery.history import HistoryArchive

class MembershipMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-membership-migration-')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'archive.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""CREATE TABLE images(id INTEGER PRIMARY KEY,local_date TEXT NOT NULL);
            CREATE TABLE block_images(block_key TEXT NOT NULL,image_id INTEGER NOT NULL,
                PRIMARY KEY(block_key,image_id));
            INSERT INTO images VALUES(1,'2026-11-10'),(2,'2026-11-10');""")

    def migrate(self, existed=True):
        with self.db: HistoryArchive._ensure_block_membership_migration(self.db,existed)

    def memberships(self):
        return self.db.execute('SELECT block_key,image_id FROM block_images ORDER BY block_key,image_id').fetchall()

    def test_legacy_partial_table_filled_once(self):
        self.db.execute("INSERT INTO block_images VALUES('2026-11-10',1)")
        self.db.execute("INSERT INTO block_images VALUES('2026-11-10#morning',1)")
        self.db.commit()
        self.migrate()
        self.assertEqual(self.memberships(),[('2026-11-10',1),('2026-11-10',2),('2026-11-10#morning',1)])
        self.reject_image_reads()
        self.migrate()
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],0)

    def reject_image_reads(self):
        def authorize(action,table,*args):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='images' else sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)

    def test_existing_v2_adopts_without_image_scan(self):
        self.db.execute('PRAGMA user_version=2')
        self.db.execute("INSERT INTO block_images SELECT local_date,id FROM images")
        self.db.commit()
        before=self.memberships()
        self.reject_image_reads()
        self.migrate()
        self.migrate()
        self.assertEqual(self.memberships(),before)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_migrations').fetchone()[0],1)

    def test_empty_interrupted_v2_table_backfills(self):
        self.db.execute('PRAGMA user_version=2')
        self.migrate()
        self.assertEqual(len(self.memberships()),2)

    def test_missing_table_backfills_even_if_marker_exists(self):
        self.migrate()
        self.db.execute('DELETE FROM block_images');self.db.commit()
        self.migrate(existed=False)
        self.assertEqual(len(self.memberships()),2)

    def test_failed_backfill_rolls_back_memberships_and_marker(self):
        self.db.executescript("""CREATE TRIGGER fail_backfill BEFORE INSERT ON block_images
            WHEN NEW.image_id=2 BEGIN SELECT RAISE(ABORT,'synthetic interruption'); END;""")
        with self.assertRaisesRegex(sqlite3.DatabaseError,'synthetic interruption'): self.migrate()
        self.assertEqual(self.memberships(),[])
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='archive_migrations'").fetchone())
        self.db.execute('DROP TRIGGER fail_backfill');self.db.commit()
        self.migrate()
        self.assertEqual(len(self.memberships()),2)

    def test_future_index_version_preserved(self):
        self.db.execute('PRAGMA user_version=37')
        self.db.execute('INSERT INTO block_images SELECT local_date,id FROM images');self.db.commit()
        self.migrate()
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],37)

class LegacyAuditCheckpoint(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cad3-legacy-audit-')
        self.addCleanup(self.temp.cleanup)
        self.archive=HistoryArchive(Path(self.temp.name))
        self.key='2026-11-10#morning'
        self.start='2026-11-10T06:00:00+00:00';self.end='2026-11-10T18:00:00+00:00'
        with self.archive.connect() as db:
            db.execute("INSERT INTO days(day,complete,start_utc,end_utc,updated_at) VALUES(?,1,?,?,?)",(self.key,self.start,self.end,'sentinel'))
            for id,created in ((1,self.start),(2,self.end)):
                db.execute("INSERT INTO images(id,local_date,username,username_key,created_at,url) VALUES(?,'2026-11-10','Synthetic','synthetic',?,'https://example.invalid/test')",(id,created))
                db.execute('INSERT INTO block_images VALUES(?,?)',(self.key,id))

    def deny_image_reads(self):
        from contextlib import contextmanager
        original=self.archive.connect
        @contextmanager
        def guarded():
            with original() as db:
                db.set_authorizer(lambda action,table,*args:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='images' else sqlite3.SQLITE_OK)
                yield db
        self.archive.connect=guarded

    def test_valid_block_not_rescanned(self):
        self.archive._audit_legacy_block_coverage()
        self.deny_image_reads()
        self.archive._audit_legacy_block_coverage()
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM legacy_block_coverage_audits').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT complete FROM days WHERE day=?',(self.key,)).fetchone()[0],1)

    def test_changed_checkpoint_requires_new_audit(self):
        self.archive._audit_legacy_block_coverage()
        with self.archive.connect() as db:db.execute("UPDATE days SET updated_at='changed' WHERE day=?",(self.key,))
        self.deny_image_reads()
        with self.assertRaises(sqlite3.DatabaseError):self.archive._audit_legacy_block_coverage()
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT updated_at FROM legacy_block_coverage_audits').fetchone()[0],'sentinel')

    def test_changed_boundary_reopens_invalid_block(self):
        self.archive._audit_legacy_block_coverage()
        with self.archive.connect() as db:db.execute("UPDATE days SET end_utc='2026-11-10T20:00:00+00:00' WHERE day=?",(self.key,))
        self.archive._audit_legacy_block_coverage()
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT complete FROM days WHERE day=?',(self.key,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM images').fetchone()[0],2)
        self.deny_image_reads();self.archive._audit_legacy_block_coverage()

if __name__=='__main__':unittest.main()

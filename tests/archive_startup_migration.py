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

if __name__=='__main__':unittest.main()

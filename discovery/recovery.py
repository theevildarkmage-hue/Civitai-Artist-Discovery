"""Durable retry planning and volume checks for unattended gallery collection."""

from datetime import datetime, timedelta
import statistics


RETRY_MINUTES = 30
RETRY_MAX_MINUTES = 360


class SuspiciousCaptureError(RuntimeError):
    pass


def volume_issue(rows: list[dict], target: dict) -> str | None:
    """Compare raw archive counts at the same coverage and time of day.

    Volume is a warning signal, not proof of missing artwork. Require three peers
    and a severe drop before deferring publication for another attempt.
    """
    day, segment = target["day"].split("#", 1)
    earliest = (datetime.fromisoformat(day) - timedelta(days=14)).date().isoformat()
    peers = [row["images"] for row in rows
             if earliest <= row["day"].split("#", 1)[0] < day
             and row["day"].endswith("#" + segment)
             and row["content_rating"] == target["content_rating"] and row["complete"]]
    if len(peers) < 3:
        return None
    typical = statistics.median(peers)
    if typical >= 1000 and target["images"] < typical * .2:
        return (f"Only {target['images']:,} image records were saved; comparable recent "
                f"blocks typically contain {round(typical):,}. Waiting to verify with Civitai.")
    return None


def volume_rows(db) -> list[dict]:
    return [dict(row) for row in db.execute("""SELECT d.day,d.complete,d.content_rating,
        COUNT(b.image_id) AS images FROM days d LEFT JOIN block_images b ON b.block_key=d.day
        WHERE d.day LIKE '%#morning' OR d.day LIKE '%#evening'
        GROUP BY d.day ORDER BY d.day""")]


class CaptureRecovery:
    def __init__(self, archive):
        self.archive = archive

    def audit(self, now: datetime) -> None:
        """Discover old false successes and work interrupted by an app shutdown."""
        stamp = now.timestamp()
        with self.archive.connect() as db:
            rows = volume_rows(db)
            for row in rows:
                if row["day"][:10] >= now.date().isoformat():
                    continue
                reason = volume_issue(rows, row) if row["complete"] else "Collection did not finish."
                if reason:
                    db.execute("""INSERT OR IGNORE INTO capture_recovery
                        (block_key,reason,restart_required,next_retry_at) VALUES(?,?,?,?)""",
                        (row["day"], reason, int(bool(row["complete"])), stamp))
                elif row["complete"]:
                    db.execute("DELETE FROM capture_recovery WHERE block_key=?", (row["day"],))

    def entries(self) -> dict[str, dict]:
        with self.archive.connect() as db:
            return {row["block_key"]: dict(row) for row in db.execute(
                "SELECT * FROM capture_recovery ORDER BY block_key")}

    def failed(self, key: str, reason: str, now: datetime, restart: bool = False,
               unreachable: bool = False) -> None:
        with self.archive.connect() as db:
            row = db.execute("SELECT attempts FROM capture_recovery WHERE block_key=?", (key,)).fetchone()
            attempts = (row[0] if row else 0) + 1
            minutes = RETRY_MAX_MINUTES if unreachable else min(
                RETRY_MAX_MINUTES, RETRY_MINUTES * 2 ** min(attempts - 1, 4))
            db.execute("""INSERT INTO capture_recovery
                (block_key,reason,attempts,restart_required,next_retry_at) VALUES(?,?,?,?,?)
                ON CONFLICT(block_key) DO UPDATE SET reason=excluded.reason,
                attempts=excluded.attempts,restart_required=MAX(restart_required,excluded.restart_required),
                next_retry_at=excluded.next_retry_at""",
                (key, reason[:500], attempts, int(restart), now.timestamp() + minutes * 60))

    def completed(self, key: str) -> None:
        with self.archive.connect() as db:
            db.execute("DELETE FROM capture_recovery WHERE block_key=?", (key,))

    def restarted(self, key: str) -> None:
        with self.archive.connect() as db:
            db.execute("UPDATE capture_recovery SET restart_required=0 WHERE block_key=?", (key,))

    def next_retry_at(self) -> float | None:
        with self.archive.connect() as db:
            return db.execute("SELECT MIN(next_retry_at) FROM capture_recovery").fetchone()[0]

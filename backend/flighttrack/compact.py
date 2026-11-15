"""Keep the database small without touching the price series.

Three things accumulate: runner-up offers (the 2nd–5th cheapest of each fetch,
kept so "was the cheap one a 2-stop redeye?" can be answered), the per-request
log, and calendar-sweep context rows. None of them are the history that
alerts and percentiles depend on — that is the `is_best = 1` series, and it
is never pruned here.

This is the one sanctioned exception to the append-only rule on
`observations`: in one transaction the delete trigger is dropped, the old
runner-up rows are removed, and the trigger is created again from
`store.db.OBSERVATIONS_NO_DELETE`. SQLite schema changes are transactional, so
a crash leaves either the old state or the new one, never a missing trigger
(and `migrate()` re-creates the triggers if anything ever removed them).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import Config
from .store.db import OBSERVATIONS_NO_DELETE, ensure_append_only, get_state, set_state, utcnow

STATE_LAST_COMPACT = "last_compact_at"


@dataclass
class CompactStats:
    runner_ups_deleted: int = 0
    attempts_deleted: int = 0
    feed_log_deleted: int = 0
    vacuumed: bool = False
    size_before: int = 0
    size_after: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        verb = "would delete" if self.dry_run else "deleted"
        s = (
            f"{verb} runner-ups={self.runner_ups_deleted} attempts={self.attempts_deleted} "
            f"feed_log={self.feed_log_deleted}"
        )
        if self.size_before:
            s += f" size {self.size_before / 1e6:.1f}→{self.size_after / 1e6:.1f} MB"
        return s


def _iso_ago(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_size(conn: sqlite3.Connection) -> int:
    """On-disk size of the main file after folding the write-ahead log into it."""
    with contextlib.suppress(sqlite3.DatabaseError):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    try:
        return os.path.getsize(path) if path and path != ":memory:" else 0
    except OSError:
        return 0


def compact(
    conn: sqlite3.Connection, cfg: Config, now: datetime | None = None, dry_run: bool = False
) -> CompactStats:
    now = now or datetime.now(UTC)
    r = cfg.retention
    stats = CompactStats(dry_run=dry_run, size_before=_db_size(conn))

    cut_r = _iso_ago(now, r.runner_up_days) if r.runner_up_days > 0 else None
    cut_a = _iso_ago(now, r.attempts_days) if r.attempts_days > 0 else None

    if cut_r:
        stats.runner_ups_deleted = conn.execute(
            "SELECT COUNT(*) FROM observations WHERE is_best = 0 AND observed_at < ?", (cut_r,)
        ).fetchone()[0]
    if cut_a:
        stats.attempts_deleted = conn.execute(
            "SELECT COUNT(*) FROM fetch_attempts WHERE at < ?", (cut_a,)
        ).fetchone()[0]
        stats.feed_log_deleted = conn.execute(
            "SELECT COUNT(*) FROM feed_log WHERE at < ?", (cut_a,)
        ).fetchone()[0]

    if dry_run:
        stats.size_after = stats.size_before
        return stats

    if cut_r and stats.runner_ups_deleted:
        conn.commit()
        try:
            conn.execute("BEGIN")
            conn.execute("DROP TRIGGER IF EXISTS observations_no_delete")
            conn.execute("DELETE FROM observations WHERE is_best = 0 AND observed_at < ?", (cut_r,))
            conn.execute(OBSERVATIONS_NO_DELETE)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            ensure_append_only(conn)  # a no-op unless something above went wrong
    if cut_a:
        conn.execute("DELETE FROM fetch_attempts WHERE at < ?", (cut_a,))
        conn.execute("DELETE FROM feed_log WHERE at < ?", (cut_a,))
        conn.commit()

    if stats.runner_ups_deleted or stats.attempts_deleted or stats.feed_log_deleted:
        conn.execute("VACUUM")
        stats.vacuumed = True
    set_state(conn, STATE_LAST_COMPACT, utcnow())
    stats.size_after = _db_size(conn)
    return stats


def due(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> bool:
    """Weekly, and only when retention is enabled."""
    if not cfg.retention.enabled:
        return False
    now = now or datetime.now(UTC)
    last = get_state(conn, STATE_LAST_COMPACT)
    return last is None or last < _iso_ago(now, 7)

"""Nightly housekeeping, and the start-up repair of work a crash left half done.

`prune` runs `compact.compact` (runner-up offers, the request log and the feed log,
per `retention:` in the tunables; never the price series), then removes
notifications, finished jobs and finished runs older than HISTORY_DAYS. A run that
a kept request-log row still points at is kept with it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from ..config import Config
from .db import iso
from .db import now as clock_now

HISTORY_DAYS = 180


def prune(conn: sqlite3.Connection, cfg: Config, *, now: datetime | None = None) -> dict[str, int]:
    """Delete old rows. Returns how many went from each table."""
    from ..compact import compact

    now = now or clock_now()
    out: dict[str, int] = {}
    if cfg.retention.enabled:
        st = compact(conn, cfg, now=now)
        out["runner_ups"] = st.runner_ups_deleted
        out["fetch_attempts"] = st.attempts_deleted
        out["feed_log"] = st.feed_log_deleted
    cutoff = iso(now - timedelta(days=HISTORY_DAYS))
    conn.commit()
    with conn:
        out["notifications"] = conn.execute(
            "DELETE FROM notifications WHERE created_at < ?", (cutoff,)
        ).rowcount
        out["jobs"] = conn.execute(
            "DELETE FROM jobs WHERE status IN ('done', 'failed') AND created_at < ?", (cutoff,)
        ).rowcount
        out["run_log"] = conn.execute(
            "DELETE FROM run_log WHERE finished_at IS NOT NULL AND started_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM fetch_attempts a WHERE a.run_id = run_log.id)",
            (cutoff,),
        ).rowcount
    return out


def recover_interrupted(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> tuple[int, int]:
    """At worker start: runs and jobs left unfinished by a crash or restart are marked
    failed (a job that keeps crashing the worker must not be retried forever; the
    owner can ask again). Returns (runs, jobs) fixed."""
    stamp = iso(now or clock_now())
    conn.commit()
    with conn:
        runs = conn.execute(
            "UPDATE run_log SET finished_at = ?, failed = MAX(failed, 1), "
            "notes = COALESCE(notes || '; ', '') || "
            "'interrupted: the worker stopped during this run' "
            "WHERE finished_at IS NULL",
            (stamp,),
        ).rowcount
        jobs = conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, "
            "error = 'interrupted: the worker restarted; try again' WHERE status = 'running'",
            (stamp,),
        ).rowcount
    return runs, jobs

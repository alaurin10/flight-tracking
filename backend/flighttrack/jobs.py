"""A small job queue in SQLite: the API (M5) queues work, the worker claims it.

Kinds the worker understands:

    fetch_trip    {"trip": <slug or id>}      that trip's date pairs go first, now if possible
    fetch_search  {"search": <slug or id>}    that search's next few date pairs, likewise
    poll_deals    {}                          poll the deal feeds now
    run_batch     {}                          one detail batch now, if the budget allows
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .store.db import utcnow

KINDS = ("fetch_trip", "fetch_search", "poll_deals", "run_batch")


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict[str, Any] | None = None,
    requested_by: str | None = None,
) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind!r}; one of {', '.join(KINDS)}")
    conn.commit()
    with conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, payload, requested_by, created_at) VALUES (?, ?, ?, ?)",
            (kind, json.dumps(payload or {}), requested_by, utcnow()),
        )
    return int(cur.lastrowid or 0)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    try:
        job["payload"] = json.loads(job.get("payload") or "{}")
    except ValueError:
        job["payload"] = {}
    return job


def claim_next(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Atomically mark the oldest queued job as running and return it."""
    conn.commit()
    with conn:
        row = conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ("
            "  SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
            ") AND status = 'queued' RETURNING *",
            (utcnow(),),
        ).fetchone()
    return _row(row) if row else None


def finish(conn: sqlite3.Connection, job_id: int, *, error: str | None = None) -> None:
    conn.commit()
    with conn:
        conn.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            ("failed" if error else "done", utcnow(), error, job_id),
        )


def get(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row(row) if row else None


def pending(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0])

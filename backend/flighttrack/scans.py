"""Repeating Google calls that are not one date pair (migration 006): calendar windows
and, from D2, Explore boards. Each has a stable key; the worker runs the stalest when its
lane's turn comes. Only an answer (prices, or a reply we could not read) counts as a run:
a block, a declined reply or a network error leaves the scan due."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from .store.db import iso

ANSWERED = ("ok", "layout")


def last_runs(conn: sqlite3.Connection, kind: str) -> dict[str, str | None]:
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT key, last_run_at FROM scans WHERE kind = ?", (kind,))
    }


def mark_active(conn: sqlite3.Connection, kind: str, keys: Iterable[str]) -> None:
    """Scans still wanted are active; the rest (past dates, removed watches) are not."""
    keys = list(keys)
    conn.execute(
        f"UPDATE scans SET active = (key IN ({','.join('?' * len(keys))})) WHERE kind = ?",
        (*keys, kind),
    )
    conn.commit()


def stalest[J](
    jobs: list[J],
    key: Callable[[J], str],
    last: dict[str, str | None],
    now: datetime,
    refresh_hours: float,
    tiebreak: Callable[[J], Any] | None = None,
) -> list[J]:
    """The jobs not run within `refresh_hours`: never run first, then oldest."""
    cut = iso(now - timedelta(hours=refresh_hours))
    due = [j for j in jobs if (last.get(key(j)) or "") < cut]
    due.sort(key=lambda j: (last.get(key(j)) or "", tiebreak(j) if tiebreak else 0))
    return due


def record(
    conn: sqlite3.Connection,
    kind: str,
    key: str,
    spec: dict[str, Any],
    outcome: str,
    now: datetime,
) -> None:
    stamp = iso(now)
    conn.execute(
        """
        INSERT INTO scans (kind, key, spec, active, last_run_at, last_ok_at, last_outcome,
                           fail_streak, created_at)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            spec = excluded.spec,
            active = 1,
            last_run_at = COALESCE(excluded.last_run_at, scans.last_run_at),
            last_ok_at = COALESCE(excluded.last_ok_at, scans.last_ok_at),
            last_outcome = excluded.last_outcome,
            fail_streak = CASE WHEN excluded.last_outcome = 'ok' THEN 0
                               ELSE scans.fail_streak + 1 END
        """,
        (
            kind,
            key,
            json.dumps(spec),
            stamp if outcome in ANSWERED else None,
            stamp if outcome == "ok" else None,
            outcome,
            0 if outcome == "ok" else 1,
            stamp,
        ),
    )


def counts(
    conn: sqlite3.Connection, kind: str, now: datetime, refresh_hours: float
) -> tuple[int, int]:
    """Active scans of a kind, and how many are older than `refresh_hours` (read only)."""
    cut = iso(now - timedelta(hours=refresh_hours))
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(last_run_at IS NULL OR last_run_at < ?), 0) FROM scans "
        "WHERE kind = ? AND active = 1",
        (cut, kind),
    ).fetchone()
    return int(row[0]), int(row[1])

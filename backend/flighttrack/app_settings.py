"""Settings the owner changes in the app, stored in the `settings` table (M5).

Only the alert thresholds that make sense to change at run time live here:
how long an alert for a date pair is not repeated, how much further a price
must fall to alert again inside that window, and whether percentile alerts are
on. Their defaults are the `alerts:` section of `config/flighttrack.yaml`; a
stored value wins. Values are stored as JSON.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3

from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .store.db import utcnow

# Model field -> the `cfg.alerts` attribute it overrides.
ALERT_KEYS = {
    "dedup_days": "dedup_days",
    "dedup_further_drop_pct": "dedup_further_drop_pct",
    "percentile_alerts": "percentile_phase2_enabled",
}
PREFIX = "alerts."


class AlertSettings(BaseModel):
    """Alert thresholds the owner can change."""

    model_config = ConfigDict(extra="forbid")

    dedup_days: int = Field(ge=1, le=60, description="days before a date pair alerts again")
    dedup_further_drop_pct: float = Field(
        ge=0, le=90, description="inside that window, alert again after a drop this large"
    )
    percentile_alerts: bool = Field(description="alert on prices in the route's cheapest 20%")


def _stored(conn: sqlite3.Connection) -> dict[str, object]:
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?", (PREFIX + "%",)
        ).fetchall()
    except sqlite3.OperationalError:  # a database from before migration 002
        return {}
    out: dict[str, object] = {}
    for row in rows:
        try:
            out[str(row[0]).removeprefix(PREFIX)] = json.loads(row[1])
        except ValueError:
            continue
    return out


def alert_settings(conn: sqlite3.Connection, cfg: Config) -> AlertSettings:
    """The thresholds in force: a stored value, else the tunables file's."""
    values: dict[str, object] = {
        field: getattr(cfg.alerts, attr) for field, attr in ALERT_KEYS.items()
    }
    stored = _stored(conn)
    for field in ALERT_KEYS:
        if field in stored:
            values[field] = stored[field]
    try:
        return AlertSettings.model_validate(values)
    except ValueError:  # a stored value out of range: fall back to the file
        return AlertSettings.model_validate(
            {field: getattr(cfg.alerts, attr) for field, attr in ALERT_KEYS.items()}
        )


def save_alert_settings(conn: sqlite3.Connection, new: AlertSettings) -> None:
    now = utcnow()
    with conn:
        for field in ALERT_KEYS:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (PREFIX + field, json.dumps(getattr(new, field)), now),
            )


def apply(conn: sqlite3.Connection, cfg: Config) -> Config:
    """`cfg` with the stored alert thresholds in place of the file's."""
    current = alert_settings(conn, cfg)
    alerts = dataclasses.replace(
        cfg.alerts, **{attr: getattr(current, field) for field, attr in ALERT_KEYS.items()}
    )
    return dataclasses.replace(cfg, alerts=alerts)

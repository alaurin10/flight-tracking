"""Nightly backups: a consistent copy of the database plus a readable list of what is
tracked (the `export-config` shape, which `import-config` reads back)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import yaml

from ..config import Config

KEEP = 14


def backup(
    conn: sqlite3.Connection,
    cfg: Config,
    backup_dir: Path,
    *,
    today: date,
    keep: int = KEEP,
) -> Path:
    """`VACUUM INTO` a dated copy, export the watches, keep the newest `keep` of each."""
    from ..watches import export_watch_file

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = today.strftime("%Y%m%d")
    target = backup_dir / f"flights-{stamp}.db"
    target.unlink(missing_ok=True)  # VACUUM INTO refuses to overwrite
    conn.commit()
    conn.execute("VACUUM INTO ?", (str(target),))
    tracked = yaml.safe_dump(export_watch_file(conn, cfg), sort_keys=False, allow_unicode=True)
    (backup_dir / f"tracked-{stamp}.yaml").write_text(
        f"# What flighttrack tracked on {today.isoformat()}. Load it with\n"
        f"# `flighttrack import-config <file>`.\n{tracked}",
        encoding="utf-8",
    )
    for pattern in ("flights-*.db", "tracked-*.yaml"):
        for old in sorted(backup_dir.glob(pattern))[:-keep]:
            old.unlink()
    return target


def list_backups(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("flights-*.db")) if backup_dir.exists() else []


def vacuum(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("VACUUM")

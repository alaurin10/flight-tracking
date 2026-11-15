"""Runtime settings, read from the environment (and `.env` in development).

Secrets and per-install values live here. Tunables (search filters, pacing,
alert thresholds) live in `config/flighttrack.yaml`; what is tracked lives in
the database.

Everything the app writes goes under `data_dir`, because the container's root
file system is read-only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # The repo-root .env, then one in the working directory, which wins if both exist.
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), extra="ignore")

    env: str = "dev"
    data_dir: Path = REPO_ROOT / "data"
    # Default: <data_dir>/flights.db (see `database`).
    db_path: Path | None = None
    config_dir: Path = REPO_ROOT / "config"
    # The built frontend (frontend/build); the image sets /app/static. None = API only.
    static_dir: Path | None = None

    # The default origin for new trips and searches, and for `doctor`.
    home_airport: str = "SEA"

    # Used from later milestones; harmless until then.
    owner_password: str = ""
    public_url: str = ""
    trusted_proxy: bool = False
    contact_email: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""

    # Public reads allowed per client per minute (`/api/airports` has its own 30). The
    # e2e tests raise it: every browser there is the same client.
    reads_per_minute: int = 60

    # The paid fallback source; empty = off. A `${SERPAPI_KEY}` in the config wins.
    serpapi_key: str = ""
    # The git commit this build came from (set by the Docker build).
    git_commit: str = ""

    # FLIGHTTRACK_SOURCE=fake: invented prices and no outside requests at all (no
    # Google, no deal feeds), for tests and demos. Empty = the real sources.
    flighttrack_source: str = ""

    @property
    def database(self) -> Path:
        return self.db_path or self.data_dir / "flights.db"

    @property
    def failure_dir(self) -> Path:
        """Pages the parser could not read, kept for diagnosis."""
        return self.data_dir / "failures"

    @property
    def backup_dir(self) -> Path:
        """Nightly database copies and watch exports (the newest 14 are kept)."""
        return self.data_dir / "backups"

    @property
    def heartbeat_path(self) -> Path:
        """Touched by the worker every 30 s; `flighttrack healthcheck worker` reads it."""
        return self.data_dir / "worker.heartbeat"

    @property
    def fake_source(self) -> bool:
        return self.flighttrack_source.strip().lower() == "fake"

    @property
    def tunables_path(self) -> Path:
        return self.config_dir / "flighttrack.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()

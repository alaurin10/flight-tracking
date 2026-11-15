"""Runtime settings, read from the environment (and `.env` in development).

Secrets and per-install values live here. Tunables (search filters, pacing,
alert thresholds) live in `config/flighttrack.yaml`; what is tracked lives in
the watch file given with `--config` (the database, from M3).

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
    static_dir: Path | None = None

    # Used from later milestones; harmless until then.
    home_airport: str = ""
    owner_password: str = ""
    public_url: str = ""
    trusted_proxy: bool = False
    contact_email: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""

    # The paid fallback source; empty = off. A `${SERPAPI_KEY}` in the config wins.
    serpapi_key: str = ""
    # The git commit this build came from (set by the Docker build).
    git_commit: str = ""

    @property
    def database(self) -> Path:
        return self.db_path or self.data_dir / "flights.db"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "out"

    @property
    def html_path(self) -> Path:
        """The static page (until the web app replaces it in M6)."""
        return self.out_dir / "index.html"

    @property
    def failure_dir(self) -> Path:
        """Pages the parser could not read, kept for diagnosis."""
        return self.data_dir / "failures"

    @property
    def tunables_path(self) -> Path:
        return self.config_dir / "flighttrack.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()

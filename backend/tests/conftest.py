"""Every test is offline and sees none of the machine's own app settings."""

from __future__ import annotations

import pytest

APP_ENV_VARS = (
    "ENV",
    "DATA_DIR",
    "DB_PATH",
    "CONFIG_DIR",
    "STATIC_DIR",
    "HOME_AIRPORT",
    "OWNER_PASSWORD",
    "PUBLIC_URL",
    "TRUSTED_PROXY",
    "CONTACT_EMAIL",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "SERPAPI_KEY",
    "GIT_COMMIT",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Tests must not see the real settings of the machine they run on, nor write to
    the repository's data/. CONFIG_DIR points at an empty folder, so `config.load`
    uses the code defaults unless a test writes its own tunables file."""
    for name in APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    base = tmp_path_factory.mktemp("app")
    monkeypatch.setenv("DATA_DIR", str(base / "data"))
    monkeypatch.setenv("CONFIG_DIR", str(base / "config"))

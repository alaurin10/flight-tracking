"""Settings from the environment, the two-file config, and JSON log lines (M2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from flighttrack.config import ConfigError, load
from flighttrack.logs import JsonFormatter, setup_logging
from flighttrack.settings import Settings


def _settings(tmp_path: Path, tunables: str | None = None, **over: object) -> Settings:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    if tunables is not None:
        (config_dir / "flighttrack.yaml").write_text(tunables)
    return Settings(data_dir=tmp_path / "data", config_dir=config_dir, **over)  # type: ignore[arg-type]


def test_settings_defaults_and_derived_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.database == tmp_path / "flights.db"
    assert s.failure_dir == tmp_path / "failures"
    assert s.env == "dev" and s.trusted_proxy is False and s.owner_password == ""
    assert s.home_airport == "SEA"

    monkeypatch.setenv("DB_PATH", str(tmp_path / "elsewhere.db"))
    monkeypatch.setenv("TRUSTED_PROXY", "1")
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    monkeypatch.setenv("HOME_AIRPORT", "pdx")
    s = Settings()
    assert s.database == tmp_path / "elsewhere.db"
    assert s.trusted_proxy is True and s.git_commit == "abc1234"
    assert load(settings=s).home == "PDX"


def test_tunables_are_read_from_config_dir(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "fetch:\n  max_queries_per_run: 25\n"
        "deals:\n  enabled: true\n  feeds: [https://feeds.example/a]\n  lookback_days: 5\n",
    )
    cfg = load(settings=settings)
    assert cfg.fetch.max_queries_per_run == 25 and cfg.fetch.min_sleep_seconds == 4.0
    assert cfg.deals.enabled and cfg.deals.feeds == ("https://feeds.example/a",)
    assert cfg.deals.lookback_days == 5


def test_watches_in_the_tunables_file_are_refused(tmp_path: Path) -> None:
    """M3: what is tracked lives in the database; the message says how to load it."""
    for text in ("home: SEA\n", "patterns: []\n", "deals:\n  watches: []\n"):
        with pytest.raises(ConfigError, match="import-config"):
            load(settings=_settings(tmp_path, text))


def test_missing_tunables_file_means_code_defaults(tmp_path: Path) -> None:
    cfg = load(settings=_settings(tmp_path))
    assert cfg.fetch.max_queries_per_run == 60 and cfg.deals.enabled is False


def test_bad_tunables_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="tunables file"):
        load(settings=_settings(tmp_path, "- just\n- a list\n"))
    with pytest.raises(ConfigError, match="not valid YAML"):
        load(settings=_settings(tmp_path, "fetch: [unclosed\n"))
    with pytest.raises(ConfigError, match="HOME_AIRPORT"):
        load(settings=_settings(tmp_path, "", home_airport="Seattle"))


def test_paths_come_from_settings_and_output_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, "output:\n  db_path: /somewhere/else.db\n")
    with caplog.at_level(logging.WARNING):
        cfg = load(settings=settings)
    assert cfg.db_path == str(tmp_path / "data" / "flights.db")
    assert cfg.failure_dir == str(tmp_path / "data" / "failures")
    assert "output: section is ignored" in caplog.text


def test_a_notify_section_is_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, "notify:\n  channel: file\n")
    assert not hasattr(load(settings=settings), "notify")
    assert "notify: section is ignored" in caplog.text


def test_serpapi_key_falls_back_to_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    settings = _settings(tmp_path, "fetch:\n  serpapi:\n    api_key: ${SERPAPI_KEY}\n")
    assert load(settings=settings).fetch.serpapi_key is None
    settings = settings.model_copy(update={"serpapi_key": "from-dotenv"})
    assert load(settings=settings).fetch.serpapi_key == "from-dotenv"
    monkeypatch.setenv("SERPAPI_KEY", "from-env")
    assert load(settings=settings).fetch.serpapi_key == "from-env"


def test_json_formatter_writes_one_object_per_line() -> None:
    record = logging.LogRecord(
        "flighttrack.fetch", logging.WARNING, __file__, 1, "[FAIL] ✈ %s", ("x",), None
    )
    line = JsonFormatter().format(record)
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["level"] == "warning" and entry["logger"] == "flighttrack.fetch"
    assert entry["msg"] == "[FAIL] ✈ x" and entry["t"].endswith("+00:00")


def test_setup_logging_json_and_plain(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        setup_logging(json_lines=True)
        logging.getLogger("flighttrack.sweep").info("[sweep] run 3")
        assert json.loads(capsys.readouterr().err.strip())["msg"] == "[sweep] run 3"
        setup_logging(json_lines=False)
        logging.getLogger("flighttrack.sweep").info("[sweep] run 4")
        assert capsys.readouterr().err == "[sweep] run 4\n"
    finally:
        root.handlers[:], root.level = saved[0], saved[1]

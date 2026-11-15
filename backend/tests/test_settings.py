"""Settings from the environment, the two-file config, and JSON log lines (M2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from flighttrack.config import ConfigError, load
from flighttrack.logs import JsonFormatter, setup_logging
from flighttrack.settings import Settings

WATCHES = """
home: SEA
routes:
  - dest: SLC
    label: Salt Lake City
patterns:
  - name: fri
    depart_dow: FRI
    nights: 3
"""


def _settings(tmp_path: Path, tunables: str | None = None) -> Settings:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    if tunables is not None:
        (config_dir / "flighttrack.yaml").write_text(tunables)
    return Settings(data_dir=tmp_path / "data", config_dir=config_dir)


def _watch_file(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "watches.yaml"
    p.write_text(WATCHES + extra)
    return p


def test_settings_defaults_and_derived_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.database == tmp_path / "flights.db"
    assert s.html_path == tmp_path / "out" / "index.html"
    assert s.failure_dir == tmp_path / "failures"
    assert s.env == "dev" and s.trusted_proxy is False and s.owner_password == ""

    monkeypatch.setenv("DB_PATH", str(tmp_path / "elsewhere.db"))
    monkeypatch.setenv("TRUSTED_PROXY", "1")
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    s = Settings()
    assert s.database == tmp_path / "elsewhere.db"
    assert s.trusted_proxy is True and s.git_commit == "abc1234"


def test_watch_file_merges_over_tunables_section_by_section(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "fetch:\n  max_queries_per_run: 25\n  min_sleep_seconds: 4\n"
        "deals:\n  feeds: [https://feeds.example/a]\n  lookback_days: 5\n"
        "calendar:\n  enabled: false\n",
    )
    cfg = load(
        _watch_file(
            tmp_path,
            "fetch:\n  min_sleep_seconds: 6\n  max_sleep_seconds: 9\n"
            "deals:\n  enabled: true\n  watches:\n    - name: w\n      origins: [Seattle]\n",
        ),
        settings=settings,
    )
    assert cfg.fetch.max_queries_per_run == 25  # from the tunables
    assert cfg.fetch.min_sleep_seconds == 6.0  # the watch file wins, key by key
    assert cfg.deals.feeds == ("https://feeds.example/a",) and cfg.deals.lookback_days == 5
    assert cfg.deals.enabled and cfg.deals.watches[0].name == "w"
    assert cfg.calendar.enabled is False


def test_missing_tunables_file_means_code_defaults(tmp_path: Path) -> None:
    cfg = load(_watch_file(tmp_path), settings=_settings(tmp_path))
    assert cfg.fetch.max_queries_per_run == 60 and cfg.deals.enabled is False


def test_bad_tunables_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="tunables file"):
        load(_watch_file(tmp_path), settings=_settings(tmp_path, "- just\n- a list\n"))
    with pytest.raises(ConfigError, match="not valid YAML"):
        load(_watch_file(tmp_path), settings=_settings(tmp_path, "fetch: [unclosed\n"))


def test_paths_come_from_settings_and_output_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    with caplog.at_level(logging.WARNING):
        cfg = load(
            _watch_file(tmp_path, "output:\n  db_path: /somewhere/else.db\n"), settings=settings
        )
    assert cfg.db_path == str(tmp_path / "data" / "flights.db")
    assert cfg.html_path == str(tmp_path / "data" / "out" / "index.html")
    assert cfg.failure_dir == str(tmp_path / "data" / "failures")
    assert "output: section is ignored" in caplog.text


def test_file_channel_writes_under_data_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "notify:\n  channel: file\n  file:\n    path: out/digest.md\n")
    cfg = load(_watch_file(tmp_path), settings=settings)
    assert cfg.notify["file"]["path"] == str(tmp_path / "data" / "out" / "digest.md")
    absolute = tmp_path / "abs.md"
    cfg = load(
        _watch_file(tmp_path, f"notify:\n  channel: file\n  file:\n    path: {absolute}\n"),
        settings=settings,
    )
    assert cfg.notify["file"]["path"] == str(absolute)


def test_serpapi_key_falls_back_to_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    tunables = "fetch:\n  serpapi:\n    api_key: ${SERPAPI_KEY}\n"
    settings = _settings(tmp_path, tunables)
    assert load(_watch_file(tmp_path), settings=settings).fetch.serpapi_key is None
    settings = settings.model_copy(update={"serpapi_key": "from-dotenv"})
    assert load(_watch_file(tmp_path), settings=settings).fetch.serpapi_key == "from-dotenv"
    monkeypatch.setenv("SERPAPI_KEY", "from-env")
    assert load(_watch_file(tmp_path), settings=settings).fetch.serpapi_key == "from-env"


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

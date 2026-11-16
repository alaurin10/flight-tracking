"""Config validation must fail loudly at load, never at 3am inside cron."""

import pytest
from helpers import load_old as load

from flighttrack.config import ConfigError

BASE = """
home: SEA
routes:
  - dest: HND
    label: Tokyo
    target_price: 65000
    priority: 1
patterns:
  - name: thu
    depart_dow: THU
    nights: 4
horizon:
  min_days_ahead: 21
  max_days_ahead: 240
"""


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_loads_a_valid_file(tmp_path):
    cfg = load(write(tmp_path, BASE))
    assert cfg.home == "SEA"
    assert cfg.routes[0].dest == "HND"
    assert cfg.patterns[0].dow_index == 3  # Thursday


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(tmp_path / "nope.yaml")


# Each case is a full config built from BASE, so what is under test is obvious
# rather than depending on a line-substitution trick.
@pytest.mark.parametrize(
    "text,match",
    [
        (BASE.replace("home: SEA", "home: SEATTLE"), "not a 3-letter IATA"),
        ("home: SEA\nroutes: HND\n", "routes: must be a list"),
        (BASE.replace("dest: HND", "dest: SEA"), "same as home"),
        (BASE.replace("target_price: 65000", "target_price: -5"), "positive integer"),
        (BASE.replace("target_price: 65000", "target_price: 450.50"), "positive integer"),
        (BASE.replace("priority: 1", "priority: 9"), "priority must be"),
        (BASE.replace("depart_dow: THU", "depart_dow: FUNDAY"), "depart_dow must be"),
        (BASE.replace("nights: 4", "nights: -2"), "non-negative integer"),
        (BASE.replace("max_days_ahead: 240", "max_days_ahead: 10"), "greater than min_days_ahead"),
        (BASE + "search:\n  seat: cattle-class\n", "search.seat must be"),
        (BASE + "search:\n  adults: 99\n", "adults must be"),
        (
            BASE.replace(
                "  - name: thu", "  - name: thu\n    depart_dow: THU\n    nights: 4\n  - name: thu"
            ),
            "duplicate pattern name",
        ),
        ("routes: []", "home"),
    ],
)
def test_rejects_bad_values(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load(write(tmp_path, text))


def test_rejects_non_mapping_root(tmp_path):
    with pytest.raises(ConfigError, match="must be a mapping"):
        load(write(tmp_path, "- just\n- a\n- list\n"))


def test_duplicate_destination_rejected(tmp_path):
    text = BASE.replace(
        "patterns:",
        "  - dest: HND\n    label: Tokyo Again\n    priority: 2\npatterns:",
    )
    with pytest.raises(ConfigError, match="duplicate destination"):
        load(write(tmp_path, text))


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-123")
    cfg = load(write(tmp_path, BASE + "fetch:\n  serpapi:\n    api_key: ${MY_KEY}\n"))
    assert cfg.fetch.serpapi_key == "secret-123"


def test_sleep_bounds_must_be_ordered(tmp_path):
    text = BASE + "\nfetch:\n  min_sleep_seconds: 10\n  max_sleep_seconds: 2\n"
    with pytest.raises(ConfigError, match="max_sleep_seconds"):
        load(write(tmp_path, text))


def test_an_old_notify_section_is_ignored_with_a_warning(tmp_path, caplog):
    """M8: alerts go by browser push; the file and smtp channels are gone."""
    text = BASE + "notify:\n  channel: smtp\n  smtp:\n    host: mail.example\n"
    cfg = load(write(tmp_path, text))
    assert not hasattr(cfg, "notify")
    assert "notify: section is ignored" in caplog.text

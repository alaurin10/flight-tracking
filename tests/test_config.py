"""Config validation must fail loudly at load, never at 3am inside cron."""

import pytest

from flighttrack.config import ConfigError, load

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
notify:
  channel: none
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
        ("home: SEA\nroutes: []\npatterns:\n  - name: t\n    depart_dow: THU\n    nights: 4\n",
         "non-empty list"),
        (BASE.replace("dest: HND", "dest: SEA"), "same as home"),
        (BASE.replace("target_price: 65000", "target_price: -5"), "positive integer"),
        (BASE.replace("target_price: 65000", "target_price: 450.50"), "positive integer"),
        (BASE.replace("priority: 1", "priority: 9"), "priority must be"),
        (BASE.replace("depart_dow: THU", "depart_dow: FUNDAY"), "depart_dow must be"),
        (BASE.replace("nights: 4", "nights: -2"), "non-negative integer"),
        (BASE.replace("max_days_ahead: 240", "max_days_ahead: 10"), "greater than min_days_ahead"),
        (BASE + "search:\n  seat: cattle-class\n", "search.seat must be"),
        (BASE + "search:\n  adults: 99\n", "adults must be"),
        (BASE.replace("channel: none", "channel: telepathy"), "notify.channel must be"),
        (BASE.replace("  - name: thu", "  - name: thu\n    depart_dow: THU\n    nights: 4\n  - name: thu"),
         "duplicate pattern name"),
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
    monkeypatch.setenv("MY_TOPIC", "s3cret-topic")
    text = BASE.replace(
        "notify:\n  channel: none",
        "notify:\n  channel: ntfy\n  ntfy:\n    topic: ${MY_TOPIC}",
    )
    cfg = load(write(tmp_path, text))
    assert cfg.notify["ntfy"]["topic"] == "s3cret-topic"


def test_unset_env_for_required_secret_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TOPIC", raising=False)
    text = BASE.replace(
        "notify:\n  channel: none",
        "notify:\n  channel: ntfy\n  ntfy:\n    topic: ${MY_TOPIC}",
    )
    with pytest.raises(ConfigError, match="ntfy.topic is empty"):
        load(write(tmp_path, text))


def test_secret_is_not_required_when_alerts_are_off(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TOPIC", raising=False)
    text = BASE.replace(
        "notify:\n  channel: none",
        "alerts:\n  enabled: false\nnotify:\n  channel: ntfy\n  ntfy:\n    topic: ${MY_TOPIC}",
    )
    assert load(write(tmp_path, text)).alerts.enabled is False


def test_sleep_bounds_must_be_ordered(tmp_path):
    text = BASE + "\nfetch:\n  min_sleep_seconds: 10\n  max_sleep_seconds: 2\n"
    with pytest.raises(ConfigError, match="max_sleep_seconds"):
        load(write(tmp_path, text))


def test_lenient_mode_allows_a_missing_secret(tmp_path, monkeypatch):
    """Read-only and diagnostic commands must not be blocked by an unset topic."""
    monkeypatch.delenv("MY_TOPIC", raising=False)
    text = BASE.replace(
        "notify:\n  channel: none",
        "notify:\n  channel: ntfy\n  ntfy:\n    topic: ${MY_TOPIC}",
    )
    cfg = load(write(tmp_path, text), strict_secrets=False)
    assert cfg.notify["ntfy"]["topic"] is None

    with pytest.raises(ConfigError, match="ntfy.topic is empty"):
        load(write(tmp_path, text), strict_secrets=True)

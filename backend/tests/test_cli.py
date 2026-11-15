"""CLI smoke tests, all offline."""

import io
import json

import pytest

from flighttrack import cli
from flighttrack.store.db import open_db

CONFIG = """
home: SEA
routes:
  - dest: HND
    label: Tokyo
    target_price: 65000
patterns:
  - name: thu
    depart_dow: THU
    nights: 4
"""


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """The database and the page go under DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _cfg(tmp_path):
    """Load the watches above through import-config, as a person would."""
    p = tmp_path / "watches.yaml"
    p.write_text(CONFIG)
    assert cli.main(["import-config", str(p)]) == 0
    return str(p)


def test_expand_status_doctor_offline_report_advise(tmp_path, capsys):
    _cfg(tmp_path)
    assert cli.main(["expand"]) == 0
    assert cli.main(["status"]) == 0
    assert cli.main(["doctor", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "query build" in out and "deep link" in out
    assert cli.main(["report", "--sort", "price"]) == 0
    assert cli.main(["advise"]) == 0
    assert cli.main(["fetch", "--dry-run"]) == 0
    assert cli.main(["sweep", "--dry-run", "--force"]) == 0


def test_ingest_creates_query_and_observation(tmp_path, monkeypatch, capsys):
    _cfg(tmp_path)
    cli.main(["expand"])
    lines = "\n".join(
        [
            json.dumps(
                {
                    "dest": "HND",
                    "depart": "2027-04-10",
                    "return": "2027-04-24",
                    "price": 812.5,
                    "airline": "ANA",
                    "stops": 0,
                    "source": "expedia",
                }
            ),
            json.dumps({"dest": "XXX", "depart": "2027-04-10", "price": 1}),
            "not json",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(lines))
    assert cli.main(["ingest"]) == 1  # two bad lines → non-zero, but the good one lands
    conn = open_db(tmp_path / "flights.db")
    row = conn.execute(
        "SELECT o.price_cents, o.source, q.pattern FROM observations o JOIN queries q ON q.id = "
        "o.query_id"
    ).fetchone()
    assert row["price_cents"] == 81250 and row["source"] == "expedia" and row["pattern"] == "manual"
    assert cli.main(["advise", "--pattern", "manual"]) == 0
    assert "HND" in capsys.readouterr().out


def test_run_orchestrates_all_stages_offline(tmp_path, capsys, monkeypatch):
    _cfg(tmp_path)
    # No network in tests: swap the source for the fake one.
    from flighttrack.source import FakeSource

    monkeypatch.setattr(cli, "_source", lambda cfg: FakeSource())
    rc = cli.main(["run", "--limit", "2", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    for stage in ("expand", "sweep", "fetch", "deals", "alert", "health", "html"):
        assert f"== {stage} ==" in out
    assert (tmp_path / "out" / "index.html").exists()

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
    for stage in ("expand", "sweep", "fetch", "deals", "alert", "health", "compact"):
        assert f"== {stage} ==" in out
    assert "== html ==" not in out and not (tmp_path / "out").exists()


def test_import_config_twice_then_export(tmp_path, capsys):
    from pathlib import Path

    old = Path(__file__).parent / "fixtures" / "config_m1.yaml"
    assert cli.main(["import-config", str(old)]) == 0
    out = capsys.readouterr().out
    assert "searches: 4 created" in out and "trips: 1 created" in out
    assert cli.main(["import-config", str(old)]) == 0
    assert "nothing changed" in capsys.readouterr().out

    exported = tmp_path / "backup.yaml"
    assert cli.main(["export-config", "--out", str(exported)]) == 0
    text = exported.read_text()
    assert "tokyo_week" in text and "Europe from Seattle" in text
    assert cli.main(["import-config", str(exported)]) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_import_config_reports_a_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("home: SEA\nroutes:\n  - dest: SEATTLE\n")
    assert cli.main(["import-config", str(bad)]) == 2
    assert "not a 3-letter IATA code" in capsys.readouterr().err


def test_healthcheck_worker_reads_the_heartbeat(tmp_path, capsys):
    import os
    import time

    assert cli.main(["healthcheck", "worker"]) == 1
    assert "missing" in capsys.readouterr().out
    beat = tmp_path / "worker.heartbeat"
    beat.touch()
    assert cli.main(["healthcheck", "worker"]) == 0
    old = time.time() - 200
    os.utime(beat, (old, old))
    assert cli.main(["healthcheck", "worker"]) == 1
    assert "200 s old" in capsys.readouterr().out


def test_worker_command_starts_the_worker_and_its_schedule(tmp_path, monkeypatch):
    """`flighttrack worker` migrates, beats once and hands over to APScheduler."""
    from flighttrack import scheduler

    started = []

    class Sched:
        def start(self):
            started.append(True)

    monkeypatch.setattr(scheduler, "build_scheduler", lambda w: Sched())
    monkeypatch.setenv("FLIGHTTRACK_SOURCE", "fake")
    assert cli.main(["worker"]) == 0
    assert started == [True] and (tmp_path / "worker.heartbeat").exists()
    conn = open_db(tmp_path / "flights.db")
    assert conn.execute("SELECT value FROM state WHERE key = 'next_batch_at'").fetchone()


def test_fake_source_switch(monkeypatch):
    from flighttrack.source import FakeSource

    monkeypatch.setenv("FLIGHTTRACK_SOURCE", "fake")
    assert isinstance(cli._source(cli.load(settings=cli.Settings())), FakeSource)

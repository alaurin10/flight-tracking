"""Transport selection: a broken primp must never stop collection."""

import pytest

from flighttrack.gflights import transport as t


def _broken(exc):
    def init(self, *a, **kw):
        raise exc

    return init


def test_auto_falls_back_to_urllib_on_any_primp_failure(monkeypatch, capsys):
    monkeypatch.setattr(t.PrimpTransport, "__init__", _broken(RuntimeError("bad wheel")))
    chosen = t.default_transport("auto")
    assert chosen.name == "urllib"
    err = capsys.readouterr().err
    assert (
        err.count("\n") == 1
        and "primp transport unavailable (RuntimeError: bad wheel); using urllib" in err
    )


def test_auto_is_quiet_when_primp_is_simply_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(
        t.PrimpTransport, "__init__", _broken(ImportError("No module named 'primp'"))
    )
    assert t.default_transport("auto").name == "urllib"
    assert capsys.readouterr().err == ""


def test_forcing_primp_surfaces_the_error(monkeypatch):
    monkeypatch.setattr(t.PrimpTransport, "__init__", _broken(RuntimeError("bad wheel")))
    with pytest.raises(RuntimeError):
        t.default_transport("primp")


def test_user_agent_matches_the_impersonated_chrome():
    major = t.IMPERSONATE.split("_")[1]
    assert f"Chrome/{major}.0.0.0" in t.USER_AGENT
    assert t.BASE_HEADERS["User-Agent"] == t.USER_AGENT

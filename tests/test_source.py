"""Source layer: our client end to end against scripted pages, and chain failover."""

import pytest

from flighttrack import source as src
from flighttrack.gflights.transport import TransportError
from helpers import NO_RESULTS_HTML, FakeTransport, make_config, make_itinerary, make_payload, make_results_html


def _cfg():
    return make_config()


def _route(cfg, dest="HND"):
    return cfg.route(dest)


def test_google_html_source_returns_normalized_offers(tmp_path):
    html = make_results_html(make_payload(best=[make_itinerary(811)], other=[make_itinerary(640, codes=("DL",))]))
    s = src.GoogleHtmlSource(transport=FakeTransport([(200, None, html)]), failures=src.FailureStore(tmp_path))
    offers = s.fetch(_cfg(), _route(_cfg()), "2027-01-07", "2027-01-11", 5)
    assert [o.price_cents for o in offers] == [64000, 81100]
    assert offers[0].airline == "Delta" and offers[0].source == "google_html" and offers[0].stops == 0
    assert offers[0].raw["price_raw"] == 640 and offers[0].raw["bucket"] == "other"
    assert s.transport.calls[0]["params"]["tfs"].startswith("Gho")
    assert not list(tmp_path.glob("*.html"))


def test_no_results_is_not_an_error(tmp_path):
    s = src.GoogleHtmlSource(transport=FakeTransport([(200, None, NO_RESULTS_HTML)]))
    with pytest.raises(src.NoResults):
        s.fetch(_cfg(), _route(_cfg()), "2027-01-07", "2027-01-11", 5)
    s = src.GoogleHtmlSource(transport=FakeTransport([(200, None, make_results_html(make_payload(best=[], other=[])))]))
    with pytest.raises(src.NoResults):
        s.fetch(_cfg(), _route(_cfg()), "2027-01-07", "2027-01-11", 5)


def test_blocked_layout_network_are_typed_and_saved(tmp_path):
    store = src.FailureStore(tmp_path, keep=5)
    s = src.GoogleHtmlSource(transport=FakeTransport([
        (429, None, "slow down"),
        (200, None, "<html><title>New</title></html>"),
        (503, None, "<html>oops</html>"),
        (404, None, "<html>gone</html>"),
        TransportError("timed out"),
    ]), failures=store)
    cfg, r = _cfg(), _route(_cfg())
    with pytest.raises(src.BlockedError):
        s.fetch(cfg, r, "2027-01-07", "2027-01-11", 5)
    with pytest.raises(src.LayoutError):
        s.fetch(cfg, r, "2027-01-07", "2027-01-11", 5)
    with pytest.raises(src.NetworkError):
        s.fetch(cfg, r, "2027-01-07", "2027-01-11", 5)
    with pytest.raises(src.LayoutError):
        s.fetch(cfg, r, "2027-01-07", "2027-01-11", 5)
    with pytest.raises(src.NetworkError):
        s.fetch(cfg, r, "2027-01-07", "2027-01-11", 5)
    kinds = sorted(p.name.split("-")[2] for p in tmp_path.glob("*.html"))
    assert kinds == ["blocked", "layout", "status", "status"]


def test_failure_store_rotates(tmp_path):
    from flighttrack.gflights.transport import Response

    store = src.FailureStore(tmp_path, keep=3)
    for i in range(6):
        store.save("layout", Response(200, "u", f"body{i}", 0.0, "fake", {}), label=f"n{i}")
    files = sorted(tmp_path.glob("*.html"))
    assert len(files) == 3 and files[-1].read_text().endswith("body5")


class _Src:
    def __init__(self, name, outcome):
        self.name, self.outcome, self.calls = name, outcome, 0

    def fetch(self, *a, **k):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_chain_falls_through_layout_and_network_but_not_blocked():
    good = [src.Offer(price_cents=50000)]
    chain = src.ChainSource([_Src("a", src.LayoutError("shape")), _Src("b", good)])
    assert chain.fetch(_cfg(), _route(_cfg()), "d", "r", 5) == good and chain.last_used == "b"

    chain = src.ChainSource([_Src("a", src.NetworkError("net")), _Src("b", good)])
    assert chain.fetch(_cfg(), _route(_cfg()), "d", "r", 5) == good

    b = _Src("b", good)
    chain = src.ChainSource([_Src("a", src.BlockedError("429")), b])
    with pytest.raises(src.BlockedError):
        chain.fetch(_cfg(), _route(_cfg()), "d", "r", 5)
    assert b.calls == 0

    b = _Src("b", good)
    chain = src.ChainSource([_Src("a", src.NoResults("none")), b])
    with pytest.raises(src.NoResults):
        chain.fetch(_cfg(), _route(_cfg()), "d", "r", 5)
    assert b.calls == 0


def test_chain_raises_most_severe_when_all_fail():
    chain = src.ChainSource([_Src("a", src.LayoutError("shape")), _Src("b", src.NetworkError("net"))])
    with pytest.raises(src.NetworkError) as ei:
        chain.fetch(_cfg(), _route(_cfg()), "d", "r", 5)
    assert "a: shape" in str(ei.value) and "b: net" in str(ei.value)


def test_classify_exception_mapping():
    assert src.classify_exception(RuntimeError("HTTP 429 Too Many Requests")).kind == "blocked"
    assert src.classify_exception(RuntimeError("connection reset by peer")).kind == "network"
    assert src.classify_exception(AttributeError("'NoneType' object has no attribute 'text'")).kind == "layout"
    assert src.classify_exception(RuntimeError("???")).kind == "unknown"


def test_build_source_respects_config(tmp_path):
    from dataclasses import replace

    cfg = replace(_cfg(), fetch=replace(_cfg().fetch, sources=("google_html",)))
    s = src.build_source(cfg, failure_dir=tmp_path, transport=FakeTransport([]))
    assert s.name == "google_html"
    cfg = replace(_cfg(), fetch=replace(_cfg().fetch, sources=("google_html", "fast_flights")))
    s = src.build_source(cfg, failure_dir=tmp_path, transport=FakeTransport([]))
    assert s.name.startswith("chain(google_html") or s.name == "google_html"   # fast-flights optional


def test_fake_source_synthetic_failures():
    f = src.FakeSource({("HND", "d", "r"): "blocked", ("NRT", "d", "r"): "layout"})
    with pytest.raises(src.BlockedError):
        f.fetch(_cfg(), _route(_cfg()), "d", "r", 1)
    with pytest.raises(src.LayoutError):
        f.fetch(_cfg(), _route(_cfg(), "NRT"), "d", "r", 1)


SERP = {
    "best_flights": [
        {"flights": [{"departure_airport": {"id": "SEA"}, "arrival_airport": {"id": "HND"}, "airline": "ANA",
                      "flight_number": "NH 117", "duration": 630, "airplane": "Boeing 787"}],
         "total_duration": 630, "price": 812},
    ],
    "other_flights": [
        {"flights": [{"departure_airport": {"id": "SEA"}, "arrival_airport": {"id": "SFO"}, "airline": "Alaska", "duration": 130},
                     {"departure_airport": {"id": "SFO"}, "arrival_airport": {"id": "HND"}, "airline": "JAL", "duration": 660}],
         "total_duration": 900, "price": 655},
        {"flights": [], "price": "n/a"},
    ],
}


def test_serpapi_source_parses_both_lists(tmp_path):
    import json

    t = FakeTransport([(200, None, json.dumps(SERP))])
    s = src.SerpApiSource("k", transport=t)
    cfg = _cfg()
    offers = s.fetch(cfg, _route(cfg), "2027-01-07", "2027-01-11", 5)
    assert [o.price_cents for o in offers] == [65500, 81200]
    assert offers[0].airline == "Alaska/JAL" and offers[0].stops == 1 and offers[0].duration_min == 900
    assert offers[1].stops == 0 and offers[1].source == "serpapi"
    p = t.calls[0]["params"]
    assert p["engine"] == "google_flights" and p["departure_id"] == "SEA" and p["return_date"] == "2027-01-11" and p["type"] == "1"


def test_serpapi_errors_are_typed():
    import json

    cfg = _cfg()
    for status, body, exc in [
        (429, "{}", src.BlockedError),
        (502, "bad gateway", src.NetworkError),
        (401, json.dumps({"error": "Invalid API key"}), src.LayoutError),
        (200, json.dumps({"error": "Google hasn't returned any results for this query."}), src.NoResults),
        (200, json.dumps({"best_flights": [], "other_flights": []}), src.NoResults),
        (200, json.dumps({"search_metadata": {}}), src.LayoutError),
    ]:
        s = src.SerpApiSource("k", transport=FakeTransport([(status, None, body)]))
        with pytest.raises(exc):
            s.fetch(cfg, _route(cfg), "2027-01-07", "2027-01-11", 5)


def test_build_source_skips_serpapi_without_key(tmp_path):
    from dataclasses import replace

    cfg = replace(_cfg(), fetch=replace(_cfg().fetch, sources=("google_html", "serpapi")))
    assert src.build_source(cfg, failure_dir=tmp_path, transport=FakeTransport([])).name == "google_html"
    cfg = replace(cfg, fetch=replace(cfg.fetch, serpapi_key="abc"))
    assert src.build_source(cfg, failure_dir=tmp_path, transport=FakeTransport([])).name == "chain(google_html>serpapi)"

"""The parsers against real Google responses captured on 2026-11-14.

Every expected value here is listed in tests/fixtures/live-20261114/README.md,
and was checked against the page rendered in Chromium at capture time.
"""

from pathlib import Path

import pytest

from flighttrack import source as src
from flighttrack.gflights.calendar import DatePrice, parse_calendar
from flighttrack.gflights.page import PageKind, classify
from flighttrack.gflights.results import itineraries, page_min_price
from flighttrack.gflights.transport import Response
from helpers import FakeTransport, make_config

FIXTURES = Path(__file__).parent / "fixtures" / "live-20261114"
RESULTS_URL = "https://www.google.com/travel/flights/search?tfs=x"


@pytest.fixture(scope="module")
def results_html():
    return (FIXTURES / "results_sea_slc_rt.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(results_html):
    return classify(Response(status=200, url=RESULTS_URL, text=results_html, elapsed=0.0, transport="fixture", headers={}))


@pytest.fixture(scope="module")
def its(page):
    return itineraries(page.payload)


# --- results_sea_slc_rt.html ---------------------------------------------------

def test_classified_as_results(page):
    assert page.kind is PageKind.RESULTS


def test_twelve_itineraries_with_the_rendered_prices(its):
    assert len(its) == 12
    assert [i.price for i in its] == [394, 447, 477, 477, 482, 527, 533, 597, 627, 627, 627, 627]


def test_cheapest_is_frontier_via_denver(its):
    c = its[0]
    assert c.price == 394 and c.airline_names == ["Frontier"]
    assert c.stops == 1 and [s.dest for s in c.segments] == ["DEN", "SLC"]
    assert [f"{s.airline_code} {s.flight_number}" for s in c.segments] == ["F9 1916", "F9 4251"]


def test_cheapest_trip_time_includes_the_layover(its):
    c = its[0]
    assert [s.duration_min for s in c.segments] == [169, 98]
    assert c.duration_min == 532                         # raw[0][9], not 169 + 98 = 267
    assert [(lay.minutes, lay.airport) for lay in c.layovers] == [(265, "DEN")]


def test_top_departing_flights_are_best(page, its):
    assert len(page.payload[2][0]) == 5
    best = sorted((i.price, i.airline_names[0]) for i in its if i.bucket == "best")
    assert best == [(447, "Delta"), (477, "Delta"), (477, "Delta"), (527, "Delta"), (533, "Alaska")]


def test_other_departing_flights_are_other(page, its):
    assert len(page.payload[3][0]) == 7
    other = sorted((i.price, i.airline_names[0]) for i in its if i.bucket == "other")
    assert other == [(394, "Frontier"), (482, "Southwest"), (597, "Delta")] + [(627, "Alaska")] * 4


def test_page_states_its_own_cheapest(page, its):
    assert page.payload[25] == [[None, 394]]
    assert page_min_price(page.payload) == 394 == its[0].price


def test_unfiltered_price_range(page):
    assert page.payload[7][0] == [[None, 304], [None, 1595]]


def test_source_reads_it_without_a_parser_mismatch(results_html):
    cfg = make_config()
    s = src.GoogleHtmlSource(transport=FakeTransport([(200, RESULTS_URL, results_html)]))
    offers = s.fetch(cfg, cfg.route("SLC"), "2027-01-15", "2027-01-18", 5)
    assert [o.price_cents for o in offers] == [39400, 44700, 47700, 47700, 48200]
    assert offers[0].airline == "Frontier" and offers[0].duration_min == 532
    assert offers[0].raw["bucket"] == "other" and offers[0].raw["layovers"] == [{"min": 265, "at": "DEN"}]
    assert all("page_min" not in o.raw for o in offers)


# --- calendar_sea_slc_3n.txt ---------------------------------------------------

@pytest.fixture(scope="module")
def calendar():
    return parse_calendar((FIXTURES / "calendar_sea_slc_3n.txt").read_text(encoding="utf-8"))


def test_calendar_has_31_dated_prices(calendar):
    assert len(calendar) == 31


def test_calendar_first_last_and_lowest(calendar):
    assert calendar[0] == DatePrice("2027-01-15", "2027-01-18", 304)     # equals the unfiltered results page
    assert calendar[-1] == DatePrice("2027-02-14", "2027-02-17", 262)
    assert min(calendar, key=lambda d: d.price) == DatePrice("2027-01-24", "2027-01-27", 200)

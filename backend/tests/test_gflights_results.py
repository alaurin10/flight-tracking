"""Itinerary extraction: both lists, defensive indices, round-trip stops."""

from helpers import make_itinerary, make_payload, make_segment

from flighttrack.gflights.results import (
    Layover,
    airline_directory,
    has_itinerary_lists,
    itineraries,
    page_min_price,
)


def test_reads_best_and_other_and_sorts_cheapest_first():
    best = [make_itinerary(900), make_itinerary(950)]
    other = [make_itinerary(640, codes=("DL",)), make_itinerary(1200)]
    its = itineraries(make_payload(best=best, other=other))
    assert [i.price for i in its] == [640, 900, 950, 1200]
    assert its[0].bucket == "other" and its[0].airline_names == ["Delta"]
    assert its[1].bucket == "best" and its[1].airline_names == ["Alaska"]


def test_duplicates_across_lists_collapse():
    same = make_itinerary(700)
    its = itineraries(make_payload(best=[same], other=[same]))
    assert len(its) == 1


def test_round_trip_stops_count_outbound_segments_only():
    two_leg = make_itinerary(800, segments=[make_segment(dest="SFO"), make_segment(origin="SFO")])
    it = itineraries(make_payload(best=[two_leg]))[0]
    assert it.stops == 1
    assert it.duration_min == 615 * 2
    assert it.segments[0].depart_time == "08:30" and it.segments[0].arrive_date == "2027-01-08"
    assert it.segments[0].flight_number == "171" and it.segments[0].airline_code == "AS"


def test_missing_fields_degrade_not_crash():
    seg = make_segment()
    seg[8] = None  # omitted time
    seg[11] = "long"  # wrong type
    seg[22] = None
    it = itineraries(make_payload(best=[make_itinerary(500, segments=[seg])]))[0]
    assert it.segments[0].depart_time is None
    assert it.segments[0].duration_min is None and it.duration_min is None
    assert it.segments[0].flight_number is None


def test_time_with_omitted_leading_zero():
    seg = make_segment(dep_t=(None, 31))
    it = itineraries(make_payload(best=[make_itinerary(500, segments=[seg])]))[0]
    assert it.segments[0].depart_time == "00:31"


def test_unpriced_and_garbage_rows_are_skipped():
    row = make_itinerary(0)
    payload = make_payload(best=[row, "garbage", None, make_itinerary(300)])
    its = itineraries(payload)
    assert [i.price for i in its] == [300, None]


def test_empty_lists_are_distinguishable_from_missing_lists():
    assert has_itinerary_lists(make_payload(best=[], other=[]))
    assert not has_itinerary_lists([None, None])
    assert itineraries(make_payload(best=None, other=None)) == []


def test_airline_directory():
    assert airline_directory(make_payload())["JL"] == "Japan Airlines"
    assert airline_directory([]) == {}


# ---------------------------------------------------------------------------
# M1: Google's own trip total, layovers, labels, the page's own cheapest price
# ---------------------------------------------------------------------------


def test_trip_time_prefers_googles_total_with_layovers():
    it = make_itinerary(
        394,
        segments=[make_segment(dest="DEN", minutes=169), make_segment(origin="DEN", minutes=98)],
    )
    it[0][9] = 532
    it[0][13] = [[265, "DEN", "DEN", None, "Denver International Airport", "Denver"]]
    parsed = itineraries(make_payload(other=[it]))[0]
    assert parsed.duration_min == 532 and parsed.total_min == 532
    assert parsed.layovers == [Layover(minutes=265, airport="DEN")]


def test_trip_time_falls_back_to_the_segments():
    it = make_itinerary(394, segments=[make_segment(minutes=169), make_segment(minutes=98)])
    for bad in (None, 0, -5, "532", True):
        it[0][9] = bad
        assert itineraries(make_payload(best=[it]))[0].duration_min == 267
    it[0][13] = "not a list"
    assert itineraries(make_payload(best=[it]))[0].layovers == []


def test_labels_follow_the_page():
    payload = [None] * 8
    payload[2] = [[make_itinerary(447)]]  # "Top departing flights"
    payload[3] = [[make_itinerary(394)]]  # "Other departing flights"
    by_price = {i.price: i.bucket for i in itineraries(payload)}
    assert by_price == {447: "best", 394: "other"}


def test_page_min_price():
    payload = make_payload(best=[make_itinerary(394)])
    assert page_min_price(payload) is None
    payload += [None] * 18
    payload[25] = [[None, 394]]
    assert page_min_price(payload) == 394
    for bad in ([[None, None]], [[None, "394"]], [[None, 0]], [], None, [[None, True]]):
        payload[25] = bad
        assert page_min_price(payload) is None

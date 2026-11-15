"""Our tfs encoder must be indistinguishable from the one known to work live."""

import itertools

import pytest

from flighttrack.gflights.tfs import Leg, Query, decode_tfs, varint


def test_varint_vectors():
    assert varint(0) == b"\x00"
    assert varint(1) == b"\x01"
    assert varint(127) == b"\x7f"
    assert varint(128) == b"\x80\x01"
    assert varint(300) == b"\xac\x02"


def test_known_round_trip_vector():
    """Hex captured from fast-flights 3.1.0 for SEA→HND 2027-01-07/11, 1 adult, carry-on, no basic economy."""
    q = Query.round_trip("SEA", "HND", "2027-01-07", "2027-01-11", carry_on_bags=1, exclude_basic_economy=True)
    assert q.encode().hex() == (
        "1a1a120a323032372d30312d30376a05120353454172051203484e44"
        "1a1a120a323032372d30312d31316a051203484e447205120353454142010148016a0410011800980101c80101"
    )
    assert q.tfs() == "GhoSCjIwMjctMDEtMDdqBRIDU0VBcgUSA0hORBoaEgoyMDI3LTAxLTExagUSA0hORHIFEgNTRUFCAQFIAWoEEAEYAJgBAcgBAQ"
    assert q.url().startswith("https://www.google.com/travel/flights/search?tfs=")
    assert "&hl=en&curr=USD" in q.url()


def test_one_way_vector_and_decode_roundtrip():
    q = Query.one_way("SEA", "SLC", "2027-02-05", max_stops=0)
    raw = q.encode()
    assert raw.startswith(b"\x1a")                       # field 3: FlightData
    assert b"\x28\x00" in raw                             # field 5 max_stops = 0 present
    assert raw.endswith(b"\x98\x01\x02")                  # trip = ONE_WAY(2)
    assert decode_tfs(q.tfs()) == raw


def test_real_google_url_shares_the_layout():
    """A tfs copied from a live Google Flights URL uses the same field numbers."""
    real = "CBwQAhoeEgoyMDI0LTA2LTAxagcIARIDU0ZPcgcIARIDSkZLGh4SCjIwMjQtMDYtMDhqBwgBEgNKRktyBwgBEgNTRk9AAUgBcAGCAQsI____________AZgBAQ"
    raw = decode_tfs(real)
    assert b"\x12\x0a2024-06-01" in raw                   # FlightData.date = field 2
    assert b"\x12\x03SFO" in raw and b"\x12\x03JFK" in raw  # Airport.airport = field 2
    assert raw.endswith(b"\x98\x01\x01")                  # Info.trip = field 19, ROUND_TRIP


@pytest.mark.parametrize("bad", [
    dict(legs=()),
    dict(legs=(Leg("2027-01-01", "SEA", "HND"),), seat="coach"),
    dict(legs=(Leg("2027-01-01", "SEA", "HND"),), adults=0),
    dict(legs=(Leg("2027-01-01", "SEA", "HND"),), adults=1, infants_on_lap=2),
])
def test_rejects_invalid_queries(bad):
    with pytest.raises(ValueError):
        Query(**bad)


def test_byte_identical_to_fast_flights_across_options():
    ff = pytest.importorskip("fast_flights")
    from fast_flights import FlightQuery, Passengers, create_query

    def theirs(rt, seat, bags, checked, ebe, hs, ms, adults, children):
        legs = [FlightQuery("2027-01-07", "SEA", "HND", max_stops=ms)]
        if rt:
            legs.append(FlightQuery("2027-01-11", "HND", "SEA", max_stops=ms))
        return create_query(
            flights=legs, seat=seat, trip="round-trip" if rt else "one-way",
            passengers=Passengers(adults=adults, children=children), currency="USD",
            carry_on_bags=bags, checked_bags=checked, exclude_basic_economy=ebe,
            hide_separate_and_self_transfer=hs,
        ).to_bytes()

    def ours(rt, seat, bags, checked, ebe, hs, ms, adults, children):
        legs = (Leg("2027-01-07", "SEA", "HND", max_stops=ms),)
        if rt:
            legs += (Leg("2027-01-11", "HND", "SEA", max_stops=ms),)
        return Query(legs, seat=seat, adults=adults, children=children, carry_on_bags=bags, checked_bags=checked,
                     exclude_basic_economy=ebe, hide_separate_and_self_transfer=hs).encode()

    n = 0
    for combo in itertools.product([True, False], ["economy", "premium-economy", "business", "first"],
                                   [0, 1], [0, 2], [True, False], [True, False], [None, 0, 1], [1, 2], [0, 1]):
        assert theirs(*combo) == ours(*combo), combo
        n += 1
    assert n > 500

"""Calendar RPC: request body shape and shape-agnostic response parsing."""

import json
import urllib.parse
from datetime import date

from flighttrack.gflights.calendar import DatePrice, build_body, parse_calendar, unwrap_batchexecute, windows


def _decode_body(body):
    assert body.startswith("f.req=") and body.endswith("&")
    outer = json.loads(urllib.parse.unquote(body[len("f.req="):-1]))
    assert outer[0] is None
    return json.loads(outer[1])


def test_round_trip_body_layout():
    inner = _decode_body(build_body("SEA", "SLC", "2027-01-01", "2027-03-31", 3, max_stops=1))
    opts = inner[1]
    assert opts[2] == 1                    # round trip
    assert opts[5] == 1                    # economy
    assert opts[6] == [1, 0, 0, 0]         # one adult
    segs = opts[13]
    assert segs[0][0] == [[["SEA", 0]]] and segs[0][1] == [[["SLC", 0]]] and segs[0][6] == "2027-01-01"
    assert segs[1][0] == [[["SLC", 0]]] and segs[1][6] == "2027-01-04"
    assert segs[0][3] == 2                 # ≤1 stop
    assert inner[2] == ["2027-01-01", "2027-03-31"]
    assert inner[4] == [3, 3]


def test_one_way_body_layout():
    inner = _decode_body(build_body("SEA", "SLC", "2027-01-01", "2027-01-31", None))
    assert inner[1][2] == 2 and len(inner[1][13]) == 1 and inner[4] is None


def test_windows_chunking_is_inclusive_and_contiguous():
    w = windows(date(2027, 1, 1), date(2027, 3, 31), size=61)
    assert w == [("2027-01-01", "2027-03-02"), ("2027-03-03", "2027-03-31")]
    assert windows(date(2027, 1, 1), date(2027, 1, 1)) == [("2027-01-01", "2027-01-01")]


BATCH = (
    ")]}'\n\n1234\n"
    '[["wrb.fr",null,"[null,[[\\"2027-01-08\\",\\"2027-01-11\\",[[null,142]],1],[\\"2027-01-15\\",\\"2027-01-18\\",[[null,98]],1],'
    '[\\"2027-01-22\\",\\"2027-01-25\\",null,0]],[2027,1]]",null,null,null,"generic"]]\n'
    "25\n[[\"di\",59],[\"af.httprm\",59,\"123\",7]]\n"
)


def test_parse_walks_to_dated_prices_and_ignores_years():
    prices = parse_calendar(BATCH)
    assert prices == [DatePrice("2027-01-08", "2027-01-11", 142), DatePrice("2027-01-15", "2027-01-18", 98)]


def test_unwrap_tolerates_garbage_lines():
    assert unwrap_batchexecute("garbage\n)]}'\nnot json\n") == []
    assert parse_calendar("") == []


def test_one_way_style_rows_without_return_date():
    text = ")]}'\n[[\"wrb.fr\",null,\"[[\\\"2027-02-01\\\",[[null,77]]]]\"]]\n"
    assert parse_calendar(text) == [DatePrice("2027-02-01", None, 77)]

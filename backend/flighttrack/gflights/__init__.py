"""Our own Google Flights client — no third-party scraper in the critical path.

The package is split by failure mode so that when Google changes something,
the module that needs attention is obvious from the error class:

    tfs.py        query → ``?tfs=`` protobuf parameter   (pure, offline, tested byte-for-byte)
    transport.py  HTTP: primp (TLS impersonation) if installed, else stdlib urllib
    page.py       classify a response: results / no results / consent / blocked / layout changed
    results.py    itinerary extraction from the embedded ``ds:1`` JSON payload
    calendar.py   EXPERIMENTAL price-calendar RPC — one call covers weeks of departure dates

Nothing here imports the rest of flighttrack; it is a self-contained client
that `source.py` adapts to the system's `Offer` type.
"""

from .tfs import Leg, Query
from .page import Page, PageKind, classify
from .results import Itinerary, Segment, itineraries
from .transport import Response, Transport, TransportError, default_transport

__all__ = [
    "Leg", "Query",
    "Page", "PageKind", "classify",
    "Itinerary", "Segment", "itineraries",
    "Response", "Transport", "TransportError", "default_transport",
]

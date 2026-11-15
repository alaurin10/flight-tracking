"""Airports with scheduled service, from `data/airports.csv` (OurAirports, public domain).

Rebuilt by `scripts/build_airports.py`. Used to check codes typed into a trip or
search and to label a new route with its city.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CSV_PATH = Path(__file__).parent / "data" / "airports.csv"


@dataclass(frozen=True)
class Airport:
    iata: str
    name: str
    city: str
    country: str


@lru_cache(maxsize=1)
def _all() -> dict[str, Airport]:
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        return {
            row["iata"]: Airport(row["iata"], row["name"], row["city"], row["country"])
            for row in csv.DictReader(fh)
        }


def get(code: str) -> Airport | None:
    return _all().get(code.strip().upper())


def city(code: str) -> str:
    """The airport's city, or the code itself when the list does not know it."""
    a = get(code)
    return a.city if a and a.city else code.strip().upper()


def search(q: str, limit: int = 10) -> list[Airport]:
    """Code first, then name or city containing the text."""
    q = q.strip().lower()
    if not q:
        return []
    exact = [a for a in _all().values() if a.iata.lower() == q]
    rest = [
        a
        for a in _all().values()
        if a not in exact and (q in a.name.lower() or q in a.city.lower())
    ]
    return (exact + sorted(rest, key=lambda a: (a.city, a.iata)))[:limit]

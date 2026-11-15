"""Build flighttrack/data/airports.csv from the public-domain OurAirports list.

    uv run python scripts/build_airports.py                 # downloads the list
    uv run python scripts/build_airports.py airports.csv    # or reads a saved copy

Keeps airports with an IATA code and scheduled service. Columns: iata, name, city,
country (ISO 3166 code). Run it by hand now and then; the output is committed.
Source: https://ourairports.com/data/ (public domain).
"""

from __future__ import annotations

import csv
import io
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = Path(__file__).resolve().parents[1] / "flighttrack" / "data" / "airports.csv"
# When two rows claim one IATA code, prefer the bigger airport.
RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2, "seaplane_base": 3}


def build(text: str) -> list[tuple[str, str, str, str]]:
    best: dict[str, tuple[int, tuple[str, str, str, str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        iata = (row.get("iata_code") or "").strip().upper()
        if len(iata) != 3 or not iata.isalpha() or row.get("scheduled_service") != "yes":
            continue
        rank = RANK.get(row.get("type") or "", 9)
        item = (
            iata,
            (row.get("name") or "").strip(),
            # "Paris (Roissy-en-France, Val-d'Oise)" → "Paris"
            re.sub(r"\s*\(.*\)\s*$", "", (row.get("municipality") or "").strip()),
            (row.get("iso_country") or "").strip(),
        )
        if iata not in best or rank < best[iata][0]:
            best[iata] = (rank, item)
    return sorted(item for _, item in best.values())


def main(argv: list[str]) -> int:
    if argv:
        text = Path(argv[0]).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(URL, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    rows = build(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["iata", "name", "city", "country"])
        w.writerows(rows)
    print(f"wrote {len(rows)} airports to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

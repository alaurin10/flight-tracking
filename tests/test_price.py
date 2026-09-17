"""Price normalization — the ingestion boundary the whole history depends on."""

import pytest

from flighttrack.source import normalize_price, price_is_plausible


@pytest.mark.parametrize(
    "raw,expected",
    [
        (811, 81100),
        (811.0, 81100),
        (811.5, 81150),
        ("811", 81100),
        ("$811", 81100),
        ("$1,234", 123400),
        ("US$1,234.50", 123450),
        ("1 234", 123400),
        ("$1.234.500", 123450000),  # European grouping: dots are not decimals
    ],
)
def test_normalizes_to_cents(raw, expected):
    assert normalize_price(raw) == expected


@pytest.mark.parametrize("raw", [None, True, False, "", "free", "$0", -5, [1], {}])
def test_rejects_junk(raw):
    with pytest.raises(ValueError):
        normalize_price(raw)


def test_minor_units_mode_does_not_rescale():
    assert normalize_price(81100, assume_major_units=False) == 81100


def test_plausibility_band_catches_unit_errors():
    assert price_is_plausible(81100)      # $811 — normal
    assert not price_is_plausible(81)     # $0.81 — units almost certainly wrong
    assert not price_is_plausible(9_000_000)

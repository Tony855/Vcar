import math

import pytest

from pydantic import ValidationError

from app.web.server import PriceUpdate, _parse_pricing_settings


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2.75", 2.75),
        ("not-a-number", 1.0),
        (None, 1.0),
        ("NaN", 1.0),
        ("Infinity", 1.0),
        ("-Infinity", 1.0),
    ],
)
def test_base_fare_uses_safe_default_for_invalid_values(raw_value, expected):
    parsed = _parse_pricing_settings([{"key": "base_fare", "value": raw_value}])
    assert parsed["base_fare"] == expected
    assert math.isfinite(parsed["base_fare"])


def test_pricing_parser_ignores_unknown_keys_and_preserves_defaults():
    parsed = _parse_pricing_settings([{"key": "support_username", "value": "operator"}])
    assert parsed == {"base_fare": 1.0, "per_km_rate": 0.5}


def test_pricing_parser_handles_both_configured_values():
    parsed = _parse_pricing_settings([
        {"key": "base_fare", "value": "3.25"},
        {"key": "per_km_rate", "value": "0.8"},
    ])
    assert parsed == {"base_fare": 3.25, "per_km_rate": 0.8}


@pytest.mark.parametrize(
    "payload",
    [
        {"base_fare": -1, "per_km": 0.5},
        {"base_fare": 1, "per_km": -0.5},
        {"base_fare": float("nan"), "per_km": 0.5},
        {"base_fare": 1, "per_km": float("inf")},
    ],
)
def test_price_update_rejects_negative_and_non_finite_values(payload):
    with pytest.raises(ValidationError):
        PriceUpdate(**payload)


def test_price_update_accepts_zero_and_positive_fares():
    parsed = PriceUpdate(base_fare=0, per_km=0.75)
    assert parsed.base_fare == 0
    assert parsed.per_km == 0.75

"""Tests for the rate limiting core (issue #2349 / #2140, story S1)."""

import pytest
from django.conf import settings

from apps.utils.rate_limit import parse_rate


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        pytest.param("2000/5m", (2000, 300), id="five-minutes"),
        pytest.param("10/30s", (10, 30), id="seconds"),
        pytest.param("1000/1h", (1000, 3600), id="hours"),
        pytest.param("100/m", (100, 60), id="bare-unit-defaults-to-one"),
    ],
)
def test_parse_rate(rate, expected):
    assert parse_rate(rate) == expected


@pytest.mark.parametrize(
    "rate",
    [
        pytest.param("2000", id="no-window"),
        pytest.param("2000/5x", id="unknown-unit"),
        pytest.param("abc/5m", id="non-numeric-count"),
        pytest.param("0/5m", id="zero-count"),
        pytest.param("2000/0m", id="zero-window"),
    ],
)
def test_parse_rate_rejects_malformed_input(rate):
    with pytest.raises(ValueError, match="Invalid rate string|Rate must have"):
        parse_rate(rate)


def test_rate_limit_settings_defaults():
    """F9, F11: enforcement ships off; every configured scope rate parses."""
    assert settings.RATE_LIMIT_ENFORCE is False
    assert settings.RATE_LIMIT_CACHE_ALIAS == "rate_limit"
    assert "rate_limit" in settings.CACHES
    for scope, config in settings.RATE_LIMITS.items():
        count, window_seconds = parse_rate(config["rate"])
        assert count > 0, scope
        assert window_seconds > 0, scope

"""Tests for the rate limiting core (issue #2349 / #2140, story S1)."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.conf import settings
from django.core.cache import caches
from django.test import override_settings

from apps.utils.rate_limit import check, parse_rate

TINY_LIMITS = {"api": {"rate": "3/5m", "fail_open": True}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()


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


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_check_counts_down_remaining():
    """F1 (core): each request decrements remaining; reset is seconds to window end."""
    first = check("api", "team", "42")
    second = check("api", "team", "42")
    assert first.allowed
    assert second.allowed
    assert first.limit == 3
    assert (first.remaining, second.remaining) == (2, 1)
    assert 0 < first.reset_seconds <= 300


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_buckets_are_independent_per_identity_and_scope():
    """F5 (core): one bucket per (scope, identity_type, identity)."""
    check("api", "team", "42")
    check("api", "team", "42")
    other_identity = check("api", "team", "43")
    other_type = check("api", "user", "42")
    assert other_identity.remaining == 2
    assert other_type.remaining == 2


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_window_reset_starts_fresh(monkeypatch):
    """F4: a new window starts counting from zero."""
    monkeypatch.setattr("apps.utils.rate_limit._now", lambda: 1_000_000)
    for _ in range(3):
        check("api", "team", "42")
    monkeypatch.setattr("apps.utils.rate_limit._now", lambda: 1_000_000 + 300)
    fresh = check("api", "team", "42")
    assert fresh.allowed
    assert fresh.remaining == 2


@override_settings(RATE_LIMITS={"api": {"rate": "2000/5m", "fail_open": True}})
def test_concurrent_requests_do_not_undercount():
    """F6: N concurrent increments against one bucket count exactly N."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: check("api", "team", "42"), range(64)))
    final = check("api", "team", "42")
    assert final.remaining == 2000 - 65


def test_unknown_scope_raises():
    with pytest.raises(KeyError):
        check("nonexistent", "team", "42")

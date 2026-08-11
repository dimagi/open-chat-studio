"""Tests for the rate limiting core (issues #2349 / #2140)."""

from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.test import RequestFactory, override_settings
from waffle import get_waffle_flag_model

from apps.teams.flags import Flags
from apps.utils.factories.team import TeamFactory
from apps.utils.rate_limit import RATE_LIMIT_EXEMPT_FLAG, check, client_ip, is_exempt, parse_rate

TINY_LIMITS = {"api": {"rate": "3/5m", "fail_open": True}}


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    caches["rate_limit"].clear()
    default_cache.clear()  # Clear all waffle and other caches


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
    """Enforcement ships off; every configured scope rate parses."""
    assert settings.RATE_LIMIT_ENFORCE is False
    assert settings.RATE_LIMIT_CACHE_ALIAS == "rate_limit"
    assert "rate_limit" in settings.CACHES
    for scope, config in settings.RATE_LIMITS.items():
        count, window_seconds = parse_rate(config["rate"])
        assert count > 0, scope
        assert window_seconds > 0, scope


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_check_counts_down_remaining():
    """Each request decrements remaining; reset is seconds to window end."""
    first = check("api", "team", "42")
    second = check("api", "team", "42")
    assert first.allowed
    assert second.allowed
    assert first.limit == 3
    assert (first.remaining, second.remaining) == (2, 1)
    assert 0 < first.reset_seconds <= 300


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_buckets_are_independent_per_identity_and_scope():
    """One bucket per (scope, identity_type, identity)."""
    check("api", "team", "42")
    check("api", "team", "42")
    other_identity = check("api", "team", "43")
    other_type = check("api", "user", "42")
    assert other_identity.remaining == 2
    assert other_type.remaining == 2


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_window_reset_starts_fresh(monkeypatch):
    """A new window starts counting from zero."""
    monkeypatch.setattr("apps.utils.rate_limit._now", lambda: 1_000_000)
    for _ in range(3):
        check("api", "team", "42")
    monkeypatch.setattr("apps.utils.rate_limit._now", lambda: 1_000_000 + 300)
    fresh = check("api", "team", "42")
    assert fresh.allowed
    assert fresh.remaining == 2


@override_settings(RATE_LIMITS={"api": {"rate": "2000/5m", "fail_open": True}})
def test_concurrent_requests_do_not_undercount():
    """N concurrent increments against one bucket count exactly N."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: check("api", "team", "42"), range(64)))
    final = check("api", "team", "42")
    assert final.remaining == 2000 - 65


def test_unknown_scope_raises():
    with pytest.raises(KeyError):
        check("nonexistent", "team", "42")


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_over_limit_blocks_when_enforcing():
    """The request over the limit is refused with retry_after set."""
    for _ in range(3):
        check("api", "team", "42")
    blocked = check("api", "team", "42")
    assert not blocked.allowed
    assert blocked.remaining == 0
    assert blocked.retry_after == blocked.reset_seconds > 0


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_over_limit_logs_would_block_when_not_enforcing(caplog):
    """Log-only mode allows the request and emits one structured log."""
    for _ in range(3):
        check("api", "team", "42")
    with caplog.at_level("INFO", logger="ocs.rate_limit"):
        result = check("api", "team", "42", team_id=7)
    assert result.allowed
    assert result.remaining == 0
    would_block = [r for r in caplog.records if r.message == "rate_limit.would_block"]
    assert len(would_block) == 1
    record = would_block[0]
    assert record.scope == "api"
    assert record.identity_type == "team"
    assert record.count == 4
    assert record.limit == 3
    assert record.team_id == 7
    assert record.key_hash != "42"


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_under_limit_logs_nothing(caplog):
    with caplog.at_level("INFO", logger="ocs.rate_limit"):
        check("api", "team", "42")
    assert not [r for r in caplog.records if r.message == "rate_limit.would_block"]


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_backend_error_fails_open_by_default(caplog):
    """A cache outage lets requests pass and logs one alertable error."""
    with mock.patch("apps.utils.rate_limit._count", side_effect=ConnectionError("redis down")):
        with caplog.at_level("ERROR", logger="ocs.rate_limit"):
            result = check("api", "team", "42")
    assert result.allowed
    assert result.degraded
    errors = [r for r in caplog.records if r.message == "rate_limit.backend_error"]
    assert len(errors) == 1


@override_settings(
    RATE_LIMITS={"api": {"rate": "3/5m", "fail_open": False}},
    RATE_LIMIT_ENFORCE=True,
)
def test_backend_error_fails_closed_when_configured(caplog):
    """A fail-closed scope refuses requests during a cache outage (the credentials scope will use this)."""
    with mock.patch("apps.utils.rate_limit._count", side_effect=ConnectionError("redis down")):
        with caplog.at_level("ERROR", logger="ocs.rate_limit"):
            result = check("api", "team", "42")
    assert not result.allowed
    assert result.degraded
    assert result.retry_after == 300


@override_settings(
    RATE_LIMITS={"api": {"rate": "3/5m", "fail_open": False}},
    RATE_LIMIT_ENFORCE=False,
)
def test_backend_error_fail_closed_still_allows_in_log_only_mode():
    """Log-only mode never blocks, even for fail-closed scopes."""
    with mock.patch("apps.utils.rate_limit._count", side_effect=ConnectionError("redis down")):
        result = check("api", "team", "42")
    assert result.allowed
    assert result.degraded


@override_settings(RATE_LIMITS=TINY_LIMITS)
def test_expired_key_between_add_and_incr_recovers():
    """The add/incr race on window expiry retries once instead of erroring."""
    real_cache = caches["rate_limit"]
    with mock.patch.object(type(real_cache), "incr", autospec=True, side_effect=[ValueError("key gone"), 1]):
        result = check("api", "team", "42")
    assert result.allowed
    assert result.remaining == 2


def _request(remote_addr="203.0.113.9", forwarded_for=None):
    headers = {"REMOTE_ADDR": remote_addr}
    if forwarded_for is not None:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return RequestFactory().get("/", **headers)


def test_client_ip_ignores_forwarded_header_without_trusted_proxies(settings):
    """X-Forwarded-For is client-controlled unless a proxy is trusted."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 0
    request = _request(remote_addr="203.0.113.9", forwarded_for="198.51.100.1")
    assert client_ip(request) == "203.0.113.9"


def test_client_ip_reads_forwarded_header_behind_trusted_proxy(settings):
    """With N trusted proxies the client is the Nth-from-right XFF entry."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 2
    request = _request(remote_addr="10.0.0.2", forwarded_for="198.51.100.1, 10.0.0.1")
    assert client_ip(request) == "198.51.100.1"


def test_client_ip_short_forwarded_header_falls_back_to_remote_addr(settings):
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 2
    request = _request(remote_addr="10.0.0.2", forwarded_for="198.51.100.1")
    assert client_ip(request) == "10.0.0.2"


def test_client_ip_buckets_ipv6_by_64(settings):
    """A single IPv6 user cannot rotate within their /64 prefix."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 0
    first = client_ip(_request(remote_addr="2001:db8:1:2::aaaa"))
    second = client_ip(_request(remote_addr="2001:db8:1:2:ffff::1"))
    assert first == second == "2001:db8:1:2::/64"


def test_client_ip_passes_through_unparseable_values(settings):
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request(remote_addr="unix-socket")) == "unix-socket"


def test_client_ip_treats_negative_proxy_count_as_untrusted(settings):
    """A misconfigured negative proxy count never reads X-Forwarded-For."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = -1
    request = _request(remote_addr="203.0.113.9", forwarded_for="198.51.100.1, 10.0.0.1")
    assert client_ip(request) == "203.0.113.9"


def test_exempt_flag_slugs_match():
    """The core's slug constant and the teams flag declaration stay in sync."""
    assert Flags.IGNORE_RATE_LIMITING.slug == RATE_LIMIT_EXEMPT_FLAG


@pytest.mark.django_db()
def test_is_exempt_for_flagged_team():
    """A team with the flag enabled is exempt; others are not."""
    flag = get_waffle_flag_model().objects.create(name=RATE_LIMIT_EXEMPT_FLAG)
    exempt_team = TeamFactory()
    other_team = TeamFactory()
    flag.teams.add(exempt_team)

    request = RequestFactory().get("/")
    request.team = exempt_team
    assert is_exempt(request)

    request.team = other_team
    assert not is_exempt(request)


@pytest.mark.django_db()
def test_is_exempt_for_everyone_acts_as_kill_switch():
    """Everyone-on disables rate limiting globally."""
    get_waffle_flag_model().objects.create(name=RATE_LIMIT_EXEMPT_FLAG, everyone=True)
    request = RequestFactory().get("/")
    assert is_exempt(request)

"""Tests for the rate limiting core (issues #2349 / #2140)."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from django.conf import settings
from django.core.cache import cache as default_cache
from django.core.cache import caches
from django.http import JsonResponse as DjangoJsonResponse
from django.test import RequestFactory, override_settings
from waffle import get_waffle_flag_model

from apps.teams.flags import Flags
from apps.utils.factories.team import TeamFactory
from apps.utils.rate_limit import (
    RATE_LIMIT_EXEMPT_FLAG,
    RateLimitHeadersMiddleware,
    RateLimitResult,
    _scope_config,
    check,
    client_ip,
    is_exempt,
    parse_rate,
    rate_limited,
)

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


@override_settings(
    RATE_LIMITS={"api": {"rate": "not-a-rate", "fail_open": True}},
    RATE_LIMIT_ENFORCE=True,
)
def test_malformed_configured_rate_degrades_instead_of_raising(caplog):
    """A typo'd RATE_LIMIT_* env var allows the request rather than raising for every caller."""
    with caplog.at_level("ERROR", logger="ocs.rate_limit"):
        result = check("api", "team", "42")
    assert result.allowed
    assert result.degraded
    errors = [r for r in caplog.records if r.message == "rate_limit.backend_error"]
    assert len(errors) == 1


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
def test_over_limit_would_block_logging_is_sampled_after_the_crossing(caplog):
    """Log-only mode logs the crossing request, then only every Nth request after that."""
    over_limit_requests = 250
    for _ in range(3):
        check("api", "team", "42")
    with caplog.at_level("INFO", logger="ocs.rate_limit"):
        for _ in range(over_limit_requests):
            check("api", "team", "42")
    would_block = [r for r in caplog.records if r.message == "rate_limit.would_block"]
    assert len(would_block) < over_limit_requests
    assert would_block[0].count == 4


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


@pytest.mark.parametrize(
    ("proxy_count", "remote_addr", "forwarded_for", "expected"),
    [
        pytest.param(0, "203.0.113.9", "198.51.100.1", "203.0.113.9", id="untrusted-xff-ignored"),
        pytest.param(2, "10.0.0.2", "198.51.100.1, 10.0.0.1", "198.51.100.1", id="trusted-proxy-reads-xff"),
        pytest.param(2, "10.0.0.2", "198.51.100.1", "10.0.0.2", id="short-xff-falls-back"),
        pytest.param(-1, "203.0.113.9", "198.51.100.1, 10.0.0.1", "203.0.113.9", id="negative-count-untrusted"),
    ],
)
def test_client_ip_proxy_trust(settings, proxy_count, remote_addr, forwarded_for, expected):
    """X-Forwarded-For is client-controlled unless N proxies are trusted; the client is
    then the Nth-from-right entry, falling back to REMOTE_ADDR."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = proxy_count
    assert client_ip(_request(remote_addr=remote_addr, forwarded_for=forwarded_for)) == expected


def test_client_ip_buckets_ipv6_by_64(settings):
    """A single IPv6 user cannot rotate within their /64 prefix."""
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 0
    first = client_ip(_request(remote_addr="2001:db8:1:2::aaaa"))
    second = client_ip(_request(remote_addr="2001:db8:1:2:ffff::1"))
    assert first == second == "2001:db8:1:2::/64"


def test_client_ip_passes_through_unparseable_values(settings):
    settings.RATE_LIMIT_TRUSTED_PROXY_COUNT = 0
    assert client_ip(_request(remote_addr="unix-socket")) == "unix-socket"


def test_exempt_flag_slugs_match():
    """The core's slug constant and the teams flag declaration stay in sync."""
    assert Flags.IGNORE_RATE_LIMITING.slug == RATE_LIMIT_EXEMPT_FLAG


@pytest.mark.django_db()
def test_is_exempt_for_flagged_team():
    """A team with the flag enabled is exempt; others are not."""
    flag, _ = get_waffle_flag_model().objects.update_or_create(
        name=RATE_LIMIT_EXEMPT_FLAG, defaults={"everyone": False}
    )
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
    get_waffle_flag_model().objects.update_or_create(name=RATE_LIMIT_EXEMPT_FLAG, defaults={"everyone": True})
    request = RequestFactory().get("/")
    assert is_exempt(request)


@rate_limited("api")
def _limited_view(request):
    return DjangoJsonResponse({"ok": True})


def _run_view(request):
    middleware = RateLimitHeadersMiddleware(_limited_view)
    return middleware(request)


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_decorator_allows_under_limit_and_middleware_emits_headers(db):
    """Success responses carry the X-RateLimit-* headers."""
    response = _run_view(_request())
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "3"
    assert response.headers["X-RateLimit-Remaining"] == "2"
    assert int(response.headers["X-RateLimit-Reset"]) > 0


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
def test_decorator_blocks_over_limit_with_contract(db):
    """Over the limit, plain views return 429 with Retry-After and the pinned JSON body."""
    for _ in range(3):
        _run_view(_request())
    response = _run_view(_request())
    assert response.status_code == 429
    assert response.headers["Retry-After"] == response.headers["X-RateLimit-Reset"]
    body = json.loads(response.content)
    assert body["detail"] == "Rate limit exceeded."
    assert body["available_in"] > 0


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=False)
def test_decorator_log_only_never_blocks(db):
    """With enforcement off the view always runs."""
    for _ in range(5):
        response = _run_view(_request())
    assert response.status_code == 200


@override_settings(RATE_LIMITS=TINY_LIMITS, RATE_LIMIT_ENFORCE=True)
@pytest.mark.django_db()
def test_decorator_skips_exempt_requests():
    """An exempt request is never counted or limited."""
    get_waffle_flag_model().objects.update_or_create(name=RATE_LIMIT_EXEMPT_FLAG, defaults={"everyone": True})
    for _ in range(5):
        response = _run_view(_request())
    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response.headers


def test_admin_api_scope_is_configured():
    """The admin_api scope parses to 100/5m, fail-open."""
    limit, window_seconds, fail_open = _scope_config("admin_api")

    assert (limit, window_seconds, fail_open) == (100, 300, True)


def test_middleware_skips_degraded_results(db):
    """Backend-failure responses carry no headers (no counter data behind them)."""

    def view(request):
        request.rate_limit_result = RateLimitResult(
            allowed=True, limit=3, remaining=3, reset_seconds=300, degraded=True
        )
        return DjangoJsonResponse({"ok": True})

    response = RateLimitHeadersMiddleware(view)(_request())
    assert "X-RateLimit-Limit" not in response.headers

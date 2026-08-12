"""Rate limiting core (#2140 / #2349): atomic fixed-window counting with one
response contract shared by the DRF throttle adapter and the plain-view decorator.
Log-only unless settings.RATE_LIMIT_ENFORCE is True.
"""

import hashlib
import ipaddress
import logging
import re
import time
from dataclasses import dataclass
from functools import cache, wraps

from django.conf import settings
from django.core.cache import caches
from django.http import JsonResponse
from waffle import flag_is_active

logger = logging.getLogger("ocs.rate_limit")

RATE_LIMIT_EXEMPT_FLAG = "flag_ignore_rate_limiting"

# Log-only mode never stops counting, so an identity sitting well over its limit keeps
# incrementing every request; logging each one is unbounded (e.g. 4000 lines/window for
# a team at 3x its limit). Log the crossing request, then sample every Nth after that.
WOULD_BLOCK_LOG_INTERVAL = 100

_RATE_RE = re.compile(r"^(?P<count>\d+)/(?P<magnitude>\d*)(?P<unit>[smh])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_rate(rate: str) -> tuple[int, int]:
    match = _RATE_RE.match(rate)
    if not match:
        raise ValueError(f"Invalid rate string: {rate!r} (expected e.g. '2000/5m')")
    count = int(match["count"])
    window_seconds = int(match["magnitude"] or 1) * _UNIT_SECONDS[match["unit"]]
    if count == 0 or window_seconds == 0:
        raise ValueError(f"Rate must have a non-zero count and window: {rate!r}")
    return count, window_seconds


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int | None = None
    degraded: bool = False


def _now() -> int:
    return int(time.time())


def _cache():
    return caches[settings.RATE_LIMIT_CACHE_ALIAS]


@cache
def _parse_rate_cached(rate: str) -> tuple[int, int]:
    return parse_rate(rate)


def _scope_config(scope: str) -> tuple[int, int, bool]:
    config = settings.RATE_LIMITS[scope]
    limit, window_seconds = _parse_rate_cached(config["rate"])
    return limit, window_seconds, config.get("fail_open", True)


def _count(cache, key: str, timeout: int) -> int:
    cache.add(key, 0, timeout=timeout)
    try:
        return cache.incr(key)
    except ValueError:
        # The key expired between add and incr; re-create it once.
        cache.add(key, 0, timeout=timeout)
        return cache.incr(key)


def check(scope: str, identity_type: str, identity: str, team_id: int | None = None) -> RateLimitResult:
    now = _now()
    limit = window_seconds = fail_open = None
    try:
        limit, window_seconds, fail_open = _scope_config(scope)
        window_start = now - (now % window_seconds)
        reset_seconds = window_start + window_seconds - now
        key = f"rl:{scope}:{window_start}:{identity_type}:{identity}"
        count = _count(_cache(), key, timeout=window_seconds + 60)
    except KeyError:
        raise
    except Exception:
        logger.exception(
            "rate_limit.backend_error",
            extra={"scope": scope, "identity_type": identity_type},
        )
        if limit is None:
            # The configured rate itself could not be parsed, so there is no limit to
            # enforce; allow the request regardless of the scope's fail_open setting.
            return RateLimitResult(allowed=True, limit=0, remaining=0, reset_seconds=0, degraded=True)
        blocked = not fail_open and settings.RATE_LIMIT_ENFORCE
        return RateLimitResult(
            allowed=not blocked,
            limit=limit,
            remaining=0 if blocked else limit,
            reset_seconds=window_seconds,
            retry_after=window_seconds if blocked else None,
            degraded=True,
        )
    remaining = max(0, limit - count)
    if count > limit:
        if settings.RATE_LIMIT_ENFORCE:
            return RateLimitResult(
                allowed=False, limit=limit, remaining=0, reset_seconds=reset_seconds, retry_after=reset_seconds
            )
        if count == limit + 1 or count % WOULD_BLOCK_LOG_INTERVAL == 0:
            logger.info(
                "rate_limit.would_block",
                extra={
                    "scope": scope,
                    "identity_type": identity_type,
                    "key_hash": hashlib.sha256(identity.encode()).hexdigest()[:12],
                    "count": count,
                    "limit": limit,
                    "team_id": team_id,
                },
            )
        return RateLimitResult(allowed=True, limit=limit, remaining=0, reset_seconds=reset_seconds)
    return RateLimitResult(allowed=True, limit=limit, remaining=remaining, reset_seconds=reset_seconds)


def client_ip(request) -> str:
    proxy_count = settings.RATE_LIMIT_TRUSTED_PROXY_COUNT
    ip = request.META.get("REMOTE_ADDR", "")
    if proxy_count > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if len(hops) >= proxy_count:
            ip = hops[-proxy_count]
    return _bucket_ip(ip)


def _bucket_ip(ip: str) -> str:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(parsed, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return ip


def is_exempt(request) -> bool:
    return bool(flag_is_active(request, RATE_LIMIT_EXEMPT_FLAG))


def rate_limited(scope: str, key_fn=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if is_exempt(request):
                return view_func(request, *args, **kwargs)
            identity_type, identity = (key_fn or _ip_key)(request)
            team = getattr(request, "team", None)
            result = check(scope, identity_type, identity, team_id=team.pk if team else None)
            request.rate_limit_result = result
            if not result.allowed:
                # Retry-After comes from the headers middleware, which owns it on both
                # this path and the DRF one.
                return JsonResponse({"detail": "Rate limit exceeded.", "available_in": result.retry_after}, status=429)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def _ip_key(request) -> tuple[str, str]:
    return "ip", client_ip(request)


class RateLimitHeadersMiddleware:
    """Sole owner of the rate limiting response headers, for both the plain-view
    decorator and the DRF throttle. On the DRF path this overwrites the Retry-After
    that rest_framework sets from `Throttled.wait`, with the same value.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        result = getattr(request, "rate_limit_result", None)
        if result is None:
            return response
        if not result.degraded:
            response["X-RateLimit-Limit"] = str(result.limit)
            response["X-RateLimit-Remaining"] = str(result.remaining)
            response["X-RateLimit-Reset"] = str(result.reset_seconds)
        if result.retry_after is not None:
            # Outside the degraded guard: a fail-closed scope blocks on backend failure,
            # and that 429 needs a retry hint even with no counter data to report.
            response["Retry-After"] = str(result.retry_after)
        return response

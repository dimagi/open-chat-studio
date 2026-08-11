"""Rate limiting core (#2140 / #2349): atomic fixed-window counting with one
response contract shared by the DRF throttle adapter and the plain-view decorator.
Log-only unless settings.RATE_LIMIT_ENFORCE is True.
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import caches

logger = logging.getLogger("ocs.rate_limit")

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


def _scope_config(scope: str) -> tuple[int, int, bool]:
    config = settings.RATE_LIMITS[scope]
    limit, window_seconds = parse_rate(config["rate"])
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
    limit, window_seconds, _fail_open = _scope_config(scope)
    now = _now()
    window_start = now - (now % window_seconds)
    reset_seconds = window_start + window_seconds - now
    key = f"rl:{scope}:{window_start}:{identity_type}:{identity}"
    count = _count(_cache(), key, timeout=window_seconds + 60)
    remaining = max(0, limit - count)
    if count > limit:
        if settings.RATE_LIMIT_ENFORCE:
            return RateLimitResult(
                allowed=False, limit=limit, remaining=0, reset_seconds=reset_seconds, retry_after=reset_seconds
            )
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

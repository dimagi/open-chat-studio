#!/usr/bin/env python3
"""GET JSON from an upstream that rate-limits, sleeping through the limit.

Both upstreams the reconciliation reads - raw.githubusercontent.com for the
LiteLLM price table, api.github.com for that file's commit history - impose
rate limits, and GitHub reports its own as 403 as often as 429, so both codes
are inspected.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

GITHUB_USER_AGENT = "ocs-reconcile-models-script/1.0"

# Used when a rate limit arrives without a usable Retry-After. Doubles per
# consecutive miss so a misbehaving server can't spin us in a tight loop.
DEFAULT_RETRY_AFTER_SECONDS = 5.0

# Ceiling on the self-chosen backoff window. An explicit Retry-After is not
# capped by it - see _retry_delay_seconds.
MAX_BACKOFF_SECONDS = 120.0

# Spread the retry so concurrent clients don't re-collide when the window opens.
RATE_LIMIT_JITTER_SECONDS = 1.0

# Backstop against a server that 429s forever, so a CI job cannot hang.
MAX_TOTAL_BURST_WAIT_SECONDS = 900.0


def _full_jitter(cap: float) -> float:
    """AWS "full jitter": sleep uniformly over the whole window rather than a
    fixed delay, so separate clients spread out instead of re-colliding."""
    return random.uniform(0, max(cap, 0.0))


def _retry_delay_seconds(exc: urllib.error.HTTPError, backoff: float) -> float:
    """How long to sleep before retrying a rate-limited request, jitter included.

    An explicit Retry-After is honoured in full: GitHub extends a secondary
    rate limit when a client retries before the window it asked for. With no
    usable header there is nothing to honour, so the delay is full jitter over
    the backoff window.
    """
    try:
        seconds = float((exc.headers or {}).get("Retry-After", ""))
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        return _full_jitter(min(backoff, MAX_BACKOFF_SECONDS))
    return seconds + _full_jitter(RATE_LIMIT_JITTER_SECONDS)


def _is_rate_limited(exc: urllib.error.HTTPError) -> bool:
    """429 always; 403 only when GitHub attributes it to a rate limit.

    GitHub answers both its hourly limit and its secondary abuse limits with
    403, so a 403 that carries neither signal is a permission error and must
    surface immediately rather than being slept on.
    """
    if exc.code == 429:
        return True
    if exc.code != 403:
        return False
    headers = exc.headers or {}
    return bool(headers.get("Retry-After")) or headers.get("x-ratelimit-remaining") == "0"


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """GET and parse JSON, sleeping through rate limits."""
    backoff = DEFAULT_RETRY_AFTER_SECONDS
    waited = 0.0
    while True:
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if not _is_rate_limited(exc):
                raise
            delay = _retry_delay_seconds(exc, backoff)
            if waited + delay > MAX_TOTAL_BURST_WAIT_SECONDS:
                print(f"  (!) rate limited beyond the {MAX_TOTAL_BURST_WAIT_SECONDS:.0f}s budget; giving up on {url}")
                raise
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            print(f"  (!) rate limited; sleeping {delay:.0f}s before retrying {url}")
            time.sleep(delay)
            waited += delay


def _github_headers() -> dict[str, str]:
    """Headers for api.github.com, authenticated when a token is in the env.

    Unauthenticated requests share 60 per hour per IP with everything else on
    the runner; a token raises that to 5 000 for the repository.
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": GITHUB_USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

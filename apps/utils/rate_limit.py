"""Rate limiting core (#2140 / #2349): atomic fixed-window counting with one
response contract shared by the DRF throttle adapter and the plain-view decorator.
Log-only unless settings.RATE_LIMIT_ENFORCE is True.
"""

import re

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

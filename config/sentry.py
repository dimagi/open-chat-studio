"""Sentry configuration helpers.

Kept in a dedicated module (rather than inline in ``settings.py``) so the scrubbing
behaviour can be imported and unit tested without initialising the SDK.
"""

from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

# Names of variables/dict keys whose values must never reach Sentry. Because we send local
# variables with every event (``attach_stacktrace=True``), any secret that lives in a stack
# frame would otherwise leak. The scrubber matches by exact (case-insensitive) name, so keep
# sensitive values named per one of these conventions.
#
# Anything holding raw secret/key material should be named to match one of these entries; prefer
# the ``encryption_key`` convention for CommCare Connect per-participant keys.
SENTRY_DENYLIST = [
    *DEFAULT_DENYLIST,
    "encryption_key",
    "encryption_key_bytes",
]


def get_event_scrubber() -> EventScrubber:
    """Build the EventScrubber used by ``sentry_sdk.init``.

    ``recursive=True`` so the denylist also reaches values nested inside dicts/lists (e.g. a key
    tucked inside a payload dict), not just top-level stack-frame locals.
    """
    return EventScrubber(denylist=SENTRY_DENYLIST, recursive=True)

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
SENTRY_SECRET_VAR_DENYLIST = [
    "encryption_key",
    "encryption_key_bytes",
    "session_token",
    "embed_key",
    "widget_token",
    "hmac_secret",
    "app_secret",
    "signing_secret",
    "auth_token",
    "secret_key",
    "secret_key_bytes",
]

# Credential headers the app authenticates with, in the form they appear on a Sentry event.
# sentry_sdk derives ``request.headers`` keys from the WSGI environ (``HTTP_X_SESSION_TOKEN`` ->
# ``X-Session-Token``), so entries here must be the hyphenated name; the scrubber's exact
# (case-insensitive) match means the underscore spellings in ``DEFAULT_DENYLIST`` never fire for a
# header. We cannot lean on the SDK's own header filtering either: ``send_default_pii=True`` (needed
# to identify users on issues) turns ``_filter_headers`` into a no-op. Add any new credential header
# here at the same time as the code that reads it.
SENTRY_HEADER_DENYLIST = [
    "x-api-key",  # hyphen spelling of DEFAULT_DENYLIST's "x_api_key"
    "x-csrftoken",  # hyphen spelling of DEFAULT_DENYLIST's "x_csrftoken"
    "x-session-token",
    "x-embed-key",
    "x-mac-digest",
    "x-ocs-webhook-secret",
    "x-telegram-bot-api-secret-token",
    "x-twilio-signature",
    "x-turn-hook-signature",
    "x-hub-signature-256",
    "x-slack-signature",
]

SENTRY_DENYLIST = [
    *DEFAULT_DENYLIST,
    *SENTRY_SECRET_VAR_DENYLIST,
    *SENTRY_HEADER_DENYLIST,
]


def get_event_scrubber() -> EventScrubber:
    """Build the EventScrubber used by ``sentry_sdk.init``.

    ``recursive=True`` so the denylist also reaches values nested inside dicts/lists (e.g. a key
    tucked inside a payload dict), not just top-level stack-frame locals.
    """
    return EventScrubber(denylist=SENTRY_DENYLIST, recursive=True)

"""Sentry configuration helpers.

Kept in a dedicated module (rather than inline in ``settings.py``) so the scrubbing
behaviour can be imported and unit tested without initialising the SDK.
"""

from collections.abc import Callable
from typing import Any

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.anthropic import AnthropicIntegration
from sentry_sdk.integrations.langchain import LangchainIntegration
from sentry_sdk.integrations.openai import OpenAIIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

# google-genai is installed only as a dependency of google-cloud-aiplatform, and importing the
# integration without it raises.
try:
    from sentry_sdk.integrations.google_genai import GoogleGenAIIntegration
except DidNotEnable:
    OPTIONAL_LLM_INTEGRATIONS: list[type[Integration]] = []
else:
    OPTIONAL_LLM_INTEGRATIONS = [GoogleGenAIIntegration]

# Names of variables/dict keys whose values must never reach Sentry. Because we send local
# variables with every event (``attach_stacktrace=True``), any secret that lives in a stack
# frame would otherwise leak. The scrubber matches by exact (case-insensitive) name, so keep
# sensitive values named per one of these conventions.
#
# Anything holding raw secret/key material should be named to match one of these entries; prefer
# the ``encryption_key`` convention for CommCare Connect per-participant keys.
#
# The denylist cannot reach a secret embedded in the repr *string* of a frame local, so a credential
# held on a pydantic model needs ``pydantic.SecretStr`` instead (or ``Field(repr=False)`` where
# SecretStr does not fit). ``apps/service_providers/tests/test_credentials.py`` enforces that.
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
    "access_token",
    "verify_token",
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


def get_disabled_integrations() -> list[Integration]:
    """Auto-enabling integrations to turn off in ``sentry_sdk.init``.

    The LLM client integrations report every failed provider call from their own callbacks, before
    OCS classifies it, so a revoked key or exhausted credit (which the chat pipeline answers and
    logs as a warning) and a transient overload (which it retries) each become a Sentry error.
    Failures OCS does not handle still reach Sentry through the Celery and Django integrations.
    LangChain's integration deactivates the OpenAI and Anthropic ones while it is active, so those
    are disabled with it. Google GenAI's is not auto-enabled in the current SDK, but disabling it
    keeps an explicit or future auto-enable from reporting the same errors.
    """
    return [
        LangchainIntegration(),
        OpenAIIntegration(),
        AnthropicIntegration(),
        *(integration() for integration in OPTIONAL_LLM_INTEGRATIONS),
    ]


# Load balancer health checks and WhiteNoise static files run through Django on every request.
UNSAMPLED_PATH_PREFIXES = ("/status/", "/static/")

# Frequent beat tasks that only find due work and enqueue a task per item. Each enqueues with
# ``delay_in_new_trace`` so that work is still sampled.
UNSAMPLED_TASKS = frozenset(
    {
        "apps.events.tasks.enqueue_timed_out_events",
        "apps.events.tasks.poll_due_scheduled_triggers",
        "apps.evaluations.tasks.coordinate_evaluation_runs",
    }
)


def make_traces_sampler(default_rate: float) -> Callable[[dict[str, Any]], float]:
    """Build the ``traces_sampler`` used by ``sentry_sdk.init``.

    A Celery task keeps the decision of the request or task that enqueued it, so a sampled webhook
    and its chat task are recorded together. A web request ignores the ``sentry-trace`` header it
    arrives with: any caller can set it, and honouring it would let them force sampling.
    """

    def traces_sampler(sampling_context: dict[str, Any]) -> float:
        if environ := sampling_context.get("wsgi_environ"):
            if environ.get("PATH_INFO", "").startswith(UNSAMPLED_PATH_PREFIXES):
                return 0.0
            return default_rate
        if (parent_sampled := sampling_context.get("parent_sampled")) is not None:
            return float(parent_sampled)
        if celery_job := sampling_context.get("celery_job"):
            if celery_job.get("task") in UNSAMPLED_TASKS:
                return 0.0
        return default_rate

    return traces_sampler

"""The stored result of an LLM provider's last connection test, and how it reads on screen.

The badge taxonomy is what the colour means, not which exception was raised:
green the credentials are verified, red the saved configuration is wrong and there is
something to fix, amber verification never reached a verdict so the credentials are probably
fine, grey nothing to report yet.
"""

import dataclasses
import hashlib
import json
from datetime import datetime

from django.utils.dateparse import parse_datetime

from apps.service_providers.exceptions import ServiceProviderConfigError

EXTRA_DATA_KEY = "connection_test"

OK = "ok"
FAILED = "failed"
INCONCLUSIVE = "inconclusive"
NO_MODEL = "no_model"
UNSUPPORTED = "unsupported"

UNTESTED = "untested"
CHANGED = "changed"

SUCCESS = "badge-success"
ERROR = "badge-error"
WARNING = "badge-warning"
MUTED = "badge-muted"


@dataclasses.dataclass(frozen=True)
class ConnectionStatus:
    """What the header renders: a badge, a sub-line, and for a failure the panel body."""

    state: str
    label: str
    badge_class: str
    sub: str
    title: str = ""
    body: str = ""
    raw: str = ""
    tested_at: datetime | None = None

    @property
    def show_detail(self) -> bool:
        return bool(self.title)

    @property
    def is_failure(self) -> bool:
        return self.badge_class == ERROR

    @property
    def alert_class(self) -> str:
        return "alert-error" if self.is_failure else "alert-warning"

    @property
    def needs_attention(self) -> bool:
        """Whether a save that produced this result should leave the user on the edit page.

        A pass needs no follow-up, and an unsupported provider type has nothing the user
        could do about it. Everything else - a rejected key, a rate limit, no model to test
        with - is worth landing on the page that shows why.
        """
        return self.state not in (OK, UNSUPPORTED)


def config_fingerprint(config: dict) -> str:
    """A stable digest of the credentials, so a test result can be tied to the config it tested.

    Every LLM provider type's config form holds credentials and nothing else - key, base
    URL, organization, api version - so hashing the whole config needs no per-type list of
    which keys count. The provider's name lives on the model rather than in config, which
    is what lets a rename leave a passing result standing.

    Blank values are dropped so that a form re-submitting an untouched optional field as ""
    doesn't read as a change against an older, sparser saved config.
    """
    populated = {k: v for k, v in sorted(config.items()) if v}
    return hashlib.sha256(json.dumps(populated, sort_keys=True, default=str).encode()).hexdigest()


def classify_failure(exc: Exception, provider_label: str, model_name: str, timeout_seconds: int) -> dict:
    """Turn a failed test into the stored fields that describe it.

    The split the wording draws is the one the reader acts on: a 4xx means their
    configuration is wrong, a 5xx or a timeout means verification never got a verdict.
    """
    # Local import: retry.py pulls in the provider SDKs, which are slow to load at startup.
    from apps.service_providers.llm_service.retry import should_retry_exception  # noqa: PLC0415

    raw = _raw_response(exc)
    if _is_timeout(exc):
        return _detail(
            INCONCLUSIVE,
            f"No response after {timeout_seconds} seconds",
            f"{provider_label} didn't answer in time, so verification didn't complete. "
            "The credentials look fine; try again shortly.",
            raw,
        )
    if isinstance(exc, ServiceProviderConfigError):
        return _detail(
            FAILED,
            "The saved configuration is incomplete",
            "A client couldn't be built from these settings, so no request was sent. Check the API base URL.",
            raw,
        )

    status = _extract_status_code(exc)
    match status:
        case 401:
            return _detail(
                FAILED,
                "Authentication failed",
                f"{provider_label} rejected the API key. Check that the key is correct and "
                "hasn't been revoked, then verify again.",
                raw,
            )
        case 403:
            return _detail(
                FAILED,
                "Permission denied",
                f"The key is valid, but it isn't allowed to use {model_name}. Check the key's "
                "project scopes, or your organization's model access.",
                raw,
            )
        case 404:
            return _detail(
                FAILED,
                "Model not available",
                f"{model_name} isn't available to this key. Pick a different model on the "
                "Models tab, or check the API base URL.",
                raw,
            )
        case 429:
            return _detail(
                INCONCLUSIVE,
                "Rate limited - verification didn't complete",
                f"{provider_label} is throttling requests for this key right now. The "
                "credentials look fine; try again in a few minutes.",
                raw,
            )

    if isinstance(status, int) and 400 <= status < 500:
        return _detail(
            FAILED,
            "The request was rejected",
            f"{provider_label} refused the verification request. Check the saved credentials and API base URL.",
            raw,
        )
    if should_retry_exception(exc) or (isinstance(status, int) and status >= 500):
        return _detail(
            INCONCLUSIVE,
            f"{provider_label} is unavailable",
            "Verification couldn't reach a verdict because the provider is failing on its own "
            "side. Your key is probably fine; try again soon.",
            raw,
        )
    return _detail(
        INCONCLUSIVE,
        "Verification didn't complete",
        f"Nothing came back from {provider_label} that says whether the credentials work. Try again shortly.",
        raw,
    )


def status_for(info: dict, current_fingerprint: str, provider_label: str) -> ConnectionStatus:
    """The status to render, from the stored result and the config as it stands now.

    Two things turn a stored result back into "Not tested": no result at all, and a result
    recorded against different credentials. Only the second can say why.
    """
    if not info.get("tested_at"):
        return ConnectionStatus(
            state=UNTESTED,
            label="Not verified",
            badge_class=MUTED,
            sub="These credentials have never been verified",
        )
    if info.get("fingerprint") != current_fingerprint:
        return ConnectionStatus(
            state=CHANGED,
            label="Not verified",
            badge_class=MUTED,
            sub="The credentials changed since they were last verified",
        )

    tested_at = parse_datetime(info.get("tested_at") or "")
    outcome = info.get("outcome")
    if outcome == OK:
        return ConnectionStatus(
            state=OK, label="Credentials verified", badge_class=SUCCESS, sub="", tested_at=tested_at
        )
    if outcome == NO_MODEL:
        return ConnectionStatus(
            state=NO_MODEL,
            label="Can't verify",
            badge_class=MUTED,
            sub="Add a model on the Models tab first",
        )
    if outcome == UNSUPPORTED:
        return ConnectionStatus(
            state=UNSUPPORTED,
            label="Not supported",
            badge_class=MUTED,
            sub=f"{provider_label} has no chat endpoint to verify against",
        )
    if outcome == INCONCLUSIVE:
        return ConnectionStatus(
            state=INCONCLUSIVE,
            label="Couldn't verify",
            badge_class=WARNING,
            sub=info.get("title", ""),
            title=info.get("title", ""),
            body=info.get("body", ""),
            raw=info.get("raw", ""),
            tested_at=tested_at,
        )
    return ConnectionStatus(
        state=FAILED,
        label="Verification failed",
        badge_class=ERROR,
        sub=info.get("title", ""),
        title=info.get("title", ""),
        body=info.get("body", ""),
        raw=info.get("raw", ""),
        tested_at=tested_at,
    )


def _detail(outcome: str, title: str, body: str, raw: str) -> dict:
    return {"outcome": outcome, "title": title, "body": body, "raw": raw}


def _is_timeout(exc: Exception) -> bool:
    import openai  # noqa: PLC0415 - heavy lib, slow startup
    from google.api_core import exceptions as google_exceptions  # noqa: PLC0415 - heavy lib, slow startup

    return isinstance(exc, (openai.APITimeoutError, google_exceptions.DeadlineExceeded, TimeoutError))


def _raw_response(exc: Exception, limit: int = 2000) -> str:
    """The provider's own words, for whoever has to debug it.

    Empty for anything raised on our side of the request rather than returned by the
    provider: a pydantic ValidationError embeds the value it rejected, and the value being
    validated here is the provider config, so its message can carry the API key itself.
    This string is persisted in `extra_data`, which - unlike `config` - is not encrypted,
    so a locally generated message is dropped rather than stored.

    Truncated because a provider is free to return a response of any size.
    """
    if _is_local_validation_error(exc):
        return ""
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[:limit] + "…"


def _is_local_validation_error(exc: Exception) -> bool:
    from pydantic import ValidationError  # noqa: PLC0415 - heavy lib, slow startup

    return any(
        isinstance(candidate, (ServiceProviderConfigError, ValidationError))
        for candidate in (exc, exc.__cause__, exc.__context__)
        if candidate is not None
    )


def _extract_status_code(exc: Exception) -> int | None:
    """Duck-types a status code off exc, falling back to its __cause__/__context__.

    Gemini is the one provider that wraps the status-bearing SDK exception rather than
    letting it propagate, so the status only survives on `__cause__`.
    """
    for candidate in (exc, exc.__cause__, exc.__context__):
        if candidate is None:
            continue
        status = getattr(candidate, "status_code", None) or getattr(candidate, "code", None)
        if isinstance(status, int):
            return status
    return None

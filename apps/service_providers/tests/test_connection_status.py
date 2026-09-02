import pytest

from apps.service_providers import connection_status as cs
from apps.service_providers.exceptions import ServiceProviderConfigError

TIMEOUT = 10


def _status_code_exception(status_code: int) -> Exception:
    """A plain exception with a `.status_code` attribute, standing in for the shape
    OpenAI/Anthropic-family SDK exceptions actually have: `openai.APIStatusError` and
    `anthropic.APIStatusError` (and every subclass, e.g. AuthenticationError) both carry
    `.status_code`, confirmed directly against the installed SDKs."""
    exc = Exception(f"status {status_code}")
    exc.status_code = status_code
    return exc


def _code_exception(code: int) -> Exception:
    """A plain exception with a `.code` attribute, standing in for Google's exception shape
    (`google.api_core.exceptions`), which uses `.code` instead of `.status_code` but with the
    same HTTP-equivalent numbering, e.g. `PermissionDenied().code == 403`."""
    exc = Exception(f"code {code}")
    exc.code = code
    return exc


def _wrapped_exception(cause: Exception) -> Exception:
    """A wrapper exception with no status of its own, chained to `cause` via `__cause__` -
    standing in for langchain_google_genai's actual pattern for an invalid Gemini API key:
    it catches google.api_core.exceptions.InvalidArgument (which does carry `.code`) and
    does `raise ChatGoogleGenerativeAIError(msg) from e`, and the wrapper itself has no
    status attribute of its own."""
    wrapper = Exception("wrapped, no status of its own")
    wrapper.__cause__ = cause
    return wrapper


def _classify(exc):
    return cs.classify_failure(exc, provider_label="OpenAI", model_name="o4-mini", timeout_seconds=TIMEOUT)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(_status_code_exception(401), cs.FAILED, id="401-is-the-users-problem"),
        pytest.param(_status_code_exception(403), cs.FAILED, id="403-is-the-users-problem"),
        pytest.param(_status_code_exception(404), cs.FAILED, id="404-is-the-users-problem"),
        pytest.param(_code_exception(401), cs.FAILED, id="google-style-401"),
        pytest.param(ServiceProviderConfigError("openai", "bad config"), cs.FAILED, id="invalid-config"),
        pytest.param(_status_code_exception(400), cs.FAILED, id="other-4xx-still-the-users-problem"),
        pytest.param(_status_code_exception(429), cs.INCONCLUSIVE, id="rate-limit-is-not-a-verdict"),
        pytest.param(_status_code_exception(503), cs.INCONCLUSIVE, id="503-is-not-a-verdict"),
        pytest.param(_status_code_exception(500), cs.INCONCLUSIVE, id="5xx-is-not-a-verdict"),
        pytest.param(_code_exception(500), cs.INCONCLUSIVE, id="google-style-500"),
        pytest.param(RuntimeError("boom"), cs.INCONCLUSIVE, id="no-status-code-at-all"),
        pytest.param(_wrapped_exception(_code_exception(400)), cs.FAILED, id="gemini-style-wrapped-cause-400"),
        pytest.param(_wrapped_exception(_status_code_exception(500)), cs.INCONCLUSIVE, id="wrapped-cause-5xx"),
        pytest.param(_wrapped_exception(RuntimeError("no status")), cs.INCONCLUSIVE, id="wrapped-cause-no-status"),
    ],
)
def test_classify_failure_outcome(exc, expected):
    """The split the badge colour draws: a 4xx means the saved configuration is wrong and
    there is something to fix; a 5xx, a rate limit or a timeout means the test never got a
    verdict, so the credentials are probably fine.

    429 is deliberately not in the failed bucket even though it is a 4xx - "check your
    credentials" is the wrong thing to tell someone who was just throttled.

    The wrapped-cause cases reproduce the bug reported against a live Gemini provider:
    langchain_google_genai catches the real, status-bearing SDK exception and re-raises its
    own wrapper with no status of its own, `from e`. Without checking `__cause__`, a
    rejected Gemini credential reads as a provider-side outage.
    """
    assert _classify(exc)["outcome"] == expected


@pytest.mark.parametrize(
    ("exc", "expected_title"),
    [
        pytest.param(_status_code_exception(401), "Authentication failed", id="401"),
        pytest.param(_status_code_exception(403), "Permission denied", id="403"),
        pytest.param(_status_code_exception(404), "Model not available", id="404"),
        pytest.param(_status_code_exception(429), "Rate limited - verification didn't complete", id="429"),
        pytest.param(ServiceProviderConfigError("openai", "bad"), "The saved configuration is incomplete", id="config"),
    ],
)
def test_classify_failure_says_which_failure_it_was(exc, expected_title):
    """Every failure used to collapse into one message. The status code is already on the
    SDK exception, so the result can say which of these it actually was."""
    assert _classify(exc)["title"] == expected_title


def test_classify_failure_names_the_model_it_tried():
    """A 403 or 404 is about one specific model, so the message has to name it - otherwise
    "permission denied" gives the reader nothing to act on."""
    assert "o4-mini" in _classify(_status_code_exception(403))["body"]
    assert "o4-mini" in _classify(_status_code_exception(404))["body"]


def test_classify_failure_keeps_the_provider_response():
    """The raw response stays one click away for whoever has to debug it."""
    raw = _classify(_status_code_exception(401))["raw"]
    assert "status 401" in raw


def test_classify_failure_does_not_persist_a_locally_generated_message():
    """A pydantic ValidationError embeds the value it rejected, and what is being validated
    here is the provider config - so its message can carry the API key itself. `raw` is
    stored in `extra_data`, which unlike `config` is not encrypted, so a message raised on
    our side of the request is dropped rather than kept.
    """
    from pydantic import BaseModel  # noqa: PLC0415 - heavy lib, slow startup
    from pydantic import ValidationError as PydanticValidationError  # noqa: PLC0415

    class _Service(BaseModel):
        anthropic_api_key: str

    with pytest.raises(PydanticValidationError) as exc_info:
        _Service(**{"wrong_field_name": "sk-ant-NOT-A-REAL-KEY"})

    pydantic_error = exc_info.value
    assert "sk-ant-NOT-A-REAL-KEY" in str(pydantic_error), "precondition: pydantic embeds the rejected input"

    wrapped = ServiceProviderConfigError("anthropic", str(pydantic_error))
    result = _classify(wrapped)

    assert result["raw"] == ""
    assert "sk-ant-NOT-A-REAL-KEY" not in str(result)
    # The part the user can act on is unaffected.
    assert result["title"] == "The saved configuration is incomplete"


def test_classify_failure_keeps_raw_for_a_real_provider_response():
    """Only locally raised messages are dropped - a provider's own response is what the
    panel exists to show."""
    assert "status 401" in _classify(_status_code_exception(401))["raw"]


def test_classify_failure_truncates_a_huge_provider_response():
    """A provider can return a response of any size, and this is stored on the row."""
    raw = _classify(RuntimeError("x" * 5000))["raw"]
    assert len(raw) <= 2001
    assert raw.endswith("…")


def test_classify_failure_handles_real_gemini_invalid_key_error():
    """Reproduces the actual bug report, using the real classes involved (not stand-ins)
    and the real chaining mechanism (`raise ... from e`, not a manually assigned
    `__cause__`): an invalid Gemini API key raises google.api_core.exceptions.InvalidArgument
    (which does carry `.code`), and langchain_google_genai re-raises it as
    ChatGoogleGenerativeAIError (which doesn't) via `raise ChatGoogleGenerativeAIError(msg)
    from e` - the exact line in the installed package.
    """
    from google.api_core import exceptions as google_exceptions  # noqa: PLC0415 - heavy lib, slow startup
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError  # noqa: PLC0415

    def _raise_like_langchain_google_genai_does():
        try:
            raise google_exceptions.InvalidArgument("API key not valid. Please pass a valid API key.")
        except google_exceptions.InvalidArgument as e:
            msg = f"Invalid argument provided to Gemini: {e}"
            raise ChatGoogleGenerativeAIError(msg) from e

    with pytest.raises(ChatGoogleGenerativeAIError) as exc_info:
        _raise_like_langchain_google_genai_does()

    wrapper = exc_info.value
    assert not hasattr(wrapper, "status_code")
    assert not hasattr(wrapper, "code")
    assert _classify(wrapper)["outcome"] == cs.FAILED


def test_classify_failure_checks_context_not_just_explicit_cause():
    """__context__ (set automatically when a new exception is raised inside an except
    block, even without `from e`) must also be checked, not just __cause__ - a provider
    integration doesn't have to use explicit chaining for the original status to still be
    recoverable."""

    def _raise_wrapped_without_explicit_chaining():
        try:
            raise _code_exception(403)
        except Exception:
            raise ValueError("wrapped without explicit chaining")  # noqa: B904 - deliberate, testing __context__

    with pytest.raises(ValueError, match="wrapped without explicit chaining") as exc_info:
        _raise_wrapped_without_explicit_chaining()

    assert _classify(exc_info.value)["outcome"] == cs.FAILED


def test_classify_failure_recognizes_openai_timeout():
    """A provider-SDK timeout isn't in RATE_LIMIT_EXCEPTIONS or carrying a 429/503 status
    code, so should_retry_exception alone misses it - the explicit timeout check has to
    catch it, and the message says how long it waited."""
    import httpx  # noqa: PLC0415 - heavy lib, slow startup
    import openai  # noqa: PLC0415 - heavy lib, slow startup

    timeout_error = openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    result = _classify(timeout_error)

    assert result["outcome"] == cs.INCONCLUSIVE
    assert result["title"] == f"No response after {TIMEOUT} seconds"


class TestConfigFingerprint:
    def test_same_config_same_fingerprint(self):
        assert cs.config_fingerprint({"openai_api_key": "sk-1"}) == cs.config_fingerprint({"openai_api_key": "sk-1"})

    def test_key_order_does_not_matter(self):
        assert cs.config_fingerprint({"a": "1", "b": "2"}) == cs.config_fingerprint({"b": "2", "a": "1"})

    def test_a_changed_credential_changes_the_fingerprint(self):
        assert cs.config_fingerprint({"openai_api_key": "sk-1"}) != cs.config_fingerprint({"openai_api_key": "sk-2"})

    def test_blank_values_are_ignored(self):
        """A form re-submitting an untouched optional field as "" must not read as a change
        against an older, sparser saved config."""
        assert cs.config_fingerprint({"openai_api_key": "sk-1"}) == cs.config_fingerprint(
            {"openai_api_key": "sk-1", "openai_organization": ""}
        )


class TestStatusFor:
    def test_never_verified(self):
        status = cs.status_for({}, "fp", "OpenAI")
        assert status.state == cs.UNTESTED
        assert status.label == "Not verified"
        assert status.badge_class == cs.MUTED

    def test_credentials_changed_since_they_were_last_verified(self):
        """A result recorded against different credentials no longer applies to these ones."""
        info = {"outcome": cs.OK, "tested_at": "2026-01-01T00:00:00Z", "fingerprint": "old"}
        status = cs.status_for(info, "new", "OpenAI")
        assert status.state == cs.CHANGED
        assert status.label == "Not verified"

    def test_a_pass_survives_an_edit_that_leaves_credentials_alone(self):
        """Renaming a provider does not change its config, so the result still holds."""
        info = {"outcome": cs.OK, "tested_at": "2026-01-01T00:00:00Z", "fingerprint": "fp"}
        assert cs.status_for(info, "fp", "OpenAI").state == cs.OK

    @pytest.mark.parametrize(
        ("outcome", "label", "badge"),
        [
            pytest.param(cs.OK, "Credentials verified", cs.SUCCESS, id="ok-is-green"),
            pytest.param(cs.FAILED, "Verification failed", cs.ERROR, id="failed-is-red"),
            pytest.param(cs.INCONCLUSIVE, "Couldn't verify", cs.WARNING, id="inconclusive-is-amber"),
            pytest.param(cs.NO_MODEL, "Can't verify", cs.MUTED, id="no-model-is-grey"),
            pytest.param(cs.UNSUPPORTED, "Not supported", cs.MUTED, id="unsupported-is-grey"),
        ],
    )
    def test_badge_per_outcome(self, outcome, label, badge):
        info = {"outcome": outcome, "tested_at": "2026-01-01T00:00:00Z", "fingerprint": "fp"}
        status = cs.status_for(info, "fp", "OpenAI")
        assert (status.label, status.badge_class) == (label, badge)

    def test_only_a_failure_gets_the_detail_panel(self):
        ok = cs.status_for({"outcome": cs.OK, "tested_at": "t", "fingerprint": "fp"}, "fp", "OpenAI")
        failed = cs.status_for(
            {"outcome": cs.FAILED, "tested_at": "t", "fingerprint": "fp", "title": "Authentication failed"},
            "fp",
            "OpenAI",
        )
        assert not ok.show_detail
        assert failed.show_detail
        assert failed.alert_class == "alert-error"

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            pytest.param(cs.OK, False, id="a-pass-needs-no-follow-up"),
            pytest.param(cs.UNSUPPORTED, False, id="nothing-the-user-could-do-about-it"),
            pytest.param(cs.FAILED, True, id="a-rejected-key-is-worth-landing-on"),
            pytest.param(cs.INCONCLUSIVE, True, id="a-rate-limit-is-worth-a-retry"),
            pytest.param(cs.NO_MODEL, True, id="add-a-model-is-a-real-next-step"),
        ],
    )
    def test_needs_attention_decides_where_a_save_lands(self, outcome, expected):
        """A save that auto-tests uses this to choose between the team list and the edit page."""
        info = {"outcome": outcome, "tested_at": "t", "fingerprint": "fp"}
        assert cs.status_for(info, "fp", "OpenAI").needs_attention is expected

    def test_an_inconclusive_result_is_not_styled_as_a_failure(self):
        """Amber says the test couldn't reach a verdict, which is not the same as a red
        "your configuration is wrong"."""
        status = cs.status_for(
            {"outcome": cs.INCONCLUSIVE, "tested_at": "t", "fingerprint": "fp", "title": "Rate limited"},
            "fp",
            "OpenAI",
        )
        assert not status.is_failure
        assert status.alert_class == "alert-warning"

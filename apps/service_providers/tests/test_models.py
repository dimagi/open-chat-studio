from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from apps.pipelines.tests.utils import content_flow_node
from apps.service_providers.exceptions import (
    ConnectionTestNotSupportedError,
    NoTestableModelError,
    ServiceProviderConfigError,
)
from apps.service_providers.llm_service.default_models import get_default_model
from apps.service_providers.models import (
    CONNECTION_TEST_TIMEOUT_SECONDS,
    LlmProvider,
    LlmProviderModel,
    LlmProviderTypes,
    classify_connection_test_failure,
)
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


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


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory.create()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory.create()


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory.create()


@pytest.fixture()
def pipeline(llm_provider, llm_provider_model):
    pipeline = PipelineFactory.create()
    node_data = {node.flow_id: None for node in pipeline.node_set.all()}
    node_data["1"] = content_flow_node(
        "1",
        "LLMResponseWithPrompt",
        label="LLM",
        params={
            "llm_provider_id": str(llm_provider.id),
            "llm_provider_model_id": str(llm_provider_model.id),
            "prompt": "You are a helpful assistant",
        },
    )
    pipeline.update_nodes_from_data(node_data)
    return pipeline


class TestServiceProviderModel:
    @pytest.mark.django_db()
    def test_provider_models_for_team_includes_global(self, llm_provider_model):
        # Global models are normally seeded by a data migration; create them explicitly so the test
        # does not depend on migration-seeded data.
        LlmProviderModelFactory.create_batch(2, team=None)
        team_models = LlmProviderModel.objects.for_team(llm_provider_model.team).all()
        # There is a single team model that we just created in the factory
        assert len([m for m in team_models if m.team == llm_provider_model.team]) == 1
        # This single team model is the only one marked as "custom"
        custom_models = [m for m in team_models if m.is_custom()]
        assert len(custom_models) == 1
        assert custom_models[0].team == llm_provider_model.team

        # The rest of the models returned are "global"
        global_models = [m for m in team_models if m.team is None]
        assert len(global_models) > 1
        assert len(global_models) == len(team_models) - 1
        assert all(not m.is_custom() for m in global_models)

    @pytest.mark.django_db()
    def test_cannot_delete_provider_models_with_associated_models(self, assistant):
        # llm provider models that are associated with another model cannot be deleted
        provider_model = assistant.llm_provider_model
        with pytest.raises(ValidationError):
            provider_model.delete()

    @pytest.mark.django_db()
    def test_cannot_delete_provider_models_with_associated_pipeline(self, pipeline):
        node = pipeline.node_set.get(flow_id="1")
        provider_model = LlmProviderModel.objects.get(id=node.params["llm_provider_model_id"])
        with pytest.raises(ValidationError, match=pipeline.name):
            provider_model.delete()

    @pytest.mark.django_db()
    def test_cannot_delete_provider_models_used_by_an_evaluator(self):
        """Evaluators reference the model by FK, so they block deletion like any other user.

        The flows that legitimately need the model gone (``_replace_custom_model_with_global``,
        the remove_deprecated_models command) repoint evaluators first.
        """
        provider_model = LlmProviderModelFactory.create()
        evaluator = EvaluatorFactory(team=provider_model.team, llm_provider_model=provider_model)

        with pytest.raises(ValidationError, match=evaluator.name):
            provider_model.delete()

    @pytest.mark.django_db()
    def test_can_delete_unassociated_provider_models(self):
        # custom llm provider models that are not attached to experiments can be deleted
        llm_provider_model = LlmProviderModelFactory.create()
        llm_provider_model.delete()

    @pytest.mark.django_db()
    def test_can_delete_unassociated_global_provider_models(self):
        # global provider models can be deleted
        global_llm_provider_model = LlmProviderModelFactory.create(team=None)
        global_llm_provider_model.delete()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(_status_code_exception(429), "retryable", id="rate-limit-status-code-429"),
        pytest.param(_status_code_exception(503), "retryable", id="rate-limit-status-code-503"),
        pytest.param(ServiceProviderConfigError("openai", "bad config"), "permission", id="invalid-config"),
        pytest.param(_status_code_exception(401), "permission", id="openai-anthropic-style-401"),
        pytest.param(_status_code_exception(403), "permission", id="openai-anthropic-style-403"),
        pytest.param(_code_exception(401), "permission", id="google-style-401"),
        pytest.param(_status_code_exception(500), "connection", id="openai-anthropic-style-500"),
        pytest.param(_code_exception(500), "connection", id="google-style-500"),
        pytest.param(RuntimeError("boom"), "connection", id="no-status-code-at-all"),
    ],
)
def test_classify_connection_test_failure(exc, expected):
    """Retryable (rate limit/timeout) is checked first and wins even over a 4xx-looking status
    code, e.g. 429 - "check your credentials" would be the wrong message for a rate limit.
    Everything else is 400-499 (or an invalid saved config, which never even reaches the
    provider) as a permission issue; 500-599 or no status code at all (a raw connection
    failure) as a connection issue. 503 is deliberately in the retryable case, not the
    connection case: should_retry_exception treats 429/503 as the same "try again" bucket.
    """
    assert classify_connection_test_failure(exc) == expected


def test_classify_connection_test_failure_recognizes_openai_timeout():
    """A provider-SDK timeout isn't in RATE_LIMIT_EXCEPTIONS or carrying a 429/503 status
    code, so should_retry_exception alone misses it - classify_connection_test_failure must
    still catch it via the explicit timeout isinstance check, same as the view already relied
    on before this classifier absorbed that check."""
    import httpx  # noqa: PLC0415 - heavy lib, slow startup
    import openai  # noqa: PLC0415 - heavy lib, slow startup

    timeout_error = openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    assert classify_connection_test_failure(timeout_error) == "retryable"


@pytest.mark.django_db()
def test_test_connection_raises_when_no_model_configured():
    """A provider with zero LlmProviderModel rows for its type has nothing to test against.

    Deletes any migration-seeded global defaults for this type first: per this app's own
    AGENTS.md, tests must not depend on how many global rows happen to exist.
    """
    provider = LlmProviderFactory()
    LlmProviderModel.objects.filter(type=provider.type).delete()
    with pytest.raises(NoTestableModelError):
        provider.test_connection()


@pytest.mark.django_db()
def test_test_connection_invokes_chat_model_with_the_configured_model():
    """The test call should use a model the provider already has configured, not a hardcoded one."""
    provider = LlmProviderFactory()
    LlmProviderModel.objects.filter(type=provider.type).delete()
    provider_model = LlmProviderModelFactory(team=provider.team, type=provider.type, name="gpt-4o-mini")

    mock_chat_model = mock.Mock()
    mock_service = mock.Mock()
    mock_service.get_chat_model.return_value = mock_chat_model

    with mock.patch.object(LlmProvider, "get_llm_service", return_value=mock_service):
        provider.test_connection()

    mock_service.get_chat_model.assert_called_once_with(provider_model.name, timeout=CONNECTION_TEST_TIMEOUT_SECONDS)
    mock_chat_model.invoke.assert_called_once()


@pytest.mark.django_db()
def test_test_connection_prefers_team_model_over_global_default():
    """When both a global default and a team-scoped model exist for the type, use the team's own."""
    provider = LlmProviderFactory()
    LlmProviderModel.objects.filter(type=provider.type).delete()
    LlmProviderModelFactory(team=None, type=provider.type, name="global-default")
    team_model = LlmProviderModelFactory(team=provider.team, type=provider.type, name="team-custom")

    mock_chat_model = mock.Mock()
    mock_service = mock.Mock()
    mock_service.get_chat_model.return_value = mock_chat_model

    with mock.patch.object(LlmProvider, "get_llm_service", return_value=mock_service):
        provider.test_connection()

    mock_service.get_chat_model.assert_called_once_with(team_model.name, timeout=CONNECTION_TEST_TIMEOUT_SECONDS)


@pytest.mark.django_db()
def test_test_connection_prefers_default_named_model_when_team_has_multiple():
    """Given several team-configured models for the type, use the one matching the provider
    type's registered default (get_default_model), since it's most likely to actually work."""
    provider = LlmProviderFactory()
    LlmProviderModel.objects.filter(type=provider.type).delete()
    LlmProviderModelFactory(team=provider.team, type=provider.type, name="some-other-model")
    default_model = LlmProviderModelFactory(
        team=provider.team, type=provider.type, name=get_default_model(provider.type).name
    )

    mock_chat_model = mock.Mock()
    mock_service = mock.Mock()
    mock_service.get_chat_model.return_value = mock_chat_model

    with mock.patch.object(LlmProvider, "get_llm_service", return_value=mock_service):
        provider.test_connection()

    mock_service.get_chat_model.assert_called_once_with(default_model.name, timeout=CONNECTION_TEST_TIMEOUT_SECONDS)


@pytest.mark.django_db()
def test_test_connection_falls_back_to_any_team_model_when_default_not_configured():
    """A team that configured a model other than the registered default must still be
    testable — the default is a preference, not a requirement. Regression coverage: naively
    filtering to the default's name with no fallback would raise NoTestableModelError here
    even though the team does have a model configured."""
    provider = LlmProviderFactory()
    LlmProviderModel.objects.filter(type=provider.type).delete()
    provider_model = LlmProviderModelFactory(team=provider.team, type=provider.type, name="some-other-model")

    mock_chat_model = mock.Mock()
    mock_service = mock.Mock()
    mock_service.get_chat_model.return_value = mock_chat_model

    with mock.patch.object(LlmProvider, "get_llm_service", return_value=mock_service):
        provider.test_connection()

    mock_service.get_chat_model.assert_called_once_with(provider_model.name, timeout=CONNECTION_TEST_TIMEOUT_SECONDS)


@pytest.mark.django_db()
def test_test_connection_raises_for_voyage_regardless_of_configured_models():
    """Voyage AI can't do chat completions at all, so it should fail the same way whether or
    not a model happens to be configured, not flip between error messages depending on that."""
    provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))
    LlmProviderModelFactory(team=provider.team, type=provider.type, name="voyage-3")

    with pytest.raises(ConnectionTestNotSupportedError):
        provider.test_connection()


@pytest.mark.django_db()
def test_test_connection_raises_not_supported_for_voyage_with_no_models():
    """Voyage AI with zero configured models must still raise ConnectionTestNotSupportedError,
    not NoTestableModelError. The two failure cases could otherwise be conflated silently:
    without the type-based short-circuit, hitting the model-lookup check first (which also
    finds nothing for Voyage) would raise the wrong exception and show the wrong message."""
    provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))
    LlmProviderModel.objects.filter(type=provider.type).delete()

    with pytest.raises(ConnectionTestNotSupportedError):
        provider.test_connection()


@pytest.mark.django_db()
def test_run_connection_test_hook_success_returns_no_warnings():
    """A successful automatic test stays silent, matching the rest of the save flow."""
    provider = LlmProviderFactory()
    with mock.patch.object(LlmProvider, "test_connection"):
        warnings = provider.run_connection_test_hook()
    assert warnings == []


@pytest.mark.django_db()
def test_run_connection_test_hook_returns_warning_on_failure():
    """A real failure produces one warning pointing at the manual retry button, without
    raising, so it can never abort the save it runs alongside. A plain exception with no
    status code classifies as a connection issue, so the warning should say so."""
    provider = LlmProviderFactory()
    with mock.patch.object(LlmProvider, "test_connection", side_effect=RuntimeError("boom")):
        warnings = provider.run_connection_test_hook()
    assert len(warnings) == 1
    assert "test connection" in warnings[0].lower()
    assert "provider's side" in warnings[0]


@pytest.mark.django_db()
def test_run_connection_test_hook_warns_with_credentials_message_for_permission_failure():
    """A rejected-credential-style failure (a status code in the 4xx range) should tell the
    user to check their credentials, same wording the manual button uses for this case."""
    provider = LlmProviderFactory()
    exc = Exception("invalid api key")
    exc.status_code = 401
    with mock.patch.object(LlmProvider, "test_connection", side_effect=exc):
        warnings = provider.run_connection_test_hook()
    assert len(warnings) == 1
    assert "credentials" in warnings[0].lower()


@pytest.mark.django_db()
def test_run_connection_test_hook_warns_with_temporary_message_for_rate_limit():
    """Regression: a rate-limited save-time test must not be reported as a credentials
    problem. A 429 status code is technically in the 4xx range that the permission bucket
    otherwise covers, but classify_connection_test_failure checks retryable failures first
    specifically to avoid this - this test locks that ordering in at the hook level, not
    just in the classifier's own unit tests, since the hook has no separate retry/timeout
    guard of its own the way the manual view does."""
    provider = LlmProviderFactory()
    exc = Exception("rate limited")
    exc.status_code = 429
    with mock.patch.object(LlmProvider, "test_connection", side_effect=exc):
        warnings = provider.run_connection_test_hook()
    assert len(warnings) == 1
    assert "credentials" not in warnings[0].lower()
    assert "temporary" in warnings[0].lower()


@pytest.mark.django_db()
def test_run_connection_test_hook_silent_when_no_model_configured():
    """No models configured yet is expected setup state right after creating a provider,
    not a problem worth warning about on every save."""
    provider = LlmProviderFactory()
    with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
        warnings = provider.run_connection_test_hook()
    assert warnings == []


@pytest.mark.django_db()
def test_run_connection_test_hook_silent_for_unsupported_provider():
    """Voyage AI's lack of chat support is inherent to the type, not an actionable problem."""
    provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))
    with mock.patch.object(LlmProvider, "test_connection", side_effect=ConnectionTestNotSupportedError(provider.type)):
        warnings = provider.run_connection_test_hook()
    assert warnings == []


@pytest.mark.django_db()
def test_run_connection_test_hook_warns_on_invalid_configuration():
    """A genuinely invalid configuration is not the same as an unsupported provider type or a
    missing model: it's a real, actionable problem, and must produce a warning rather than
    being silently swallowed alongside the two expected setup-state cases."""
    provider = LlmProviderFactory()
    with mock.patch.object(
        LlmProvider, "test_connection", side_effect=ServiceProviderConfigError(provider.type, "bad config")
    ):
        warnings = provider.run_connection_test_hook()
    assert len(warnings) == 1
    assert "credentials" in warnings[0].lower()

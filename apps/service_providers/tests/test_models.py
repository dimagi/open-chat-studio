from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from apps.pipelines.tests.utils import content_flow_node
from apps.service_providers.connection_status import EXTRA_DATA_KEY
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


def _wrapped_exception(cause: Exception) -> Exception:
    """A wrapper exception with no status of its own, chained to `cause` via `__cause__` -
    standing in for langchain_google_genai's actual pattern for an invalid Gemini API key:
    it catches google.api_core.exceptions.InvalidArgument (which does carry `.code`) and
    does `raise ChatGoogleGenerativeAIError(msg) from e`, and the wrapper itself has no
    status attribute of its own."""
    wrapper = Exception("wrapped, no status of its own")
    wrapper.__cause__ = cause
    return wrapper


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
class TestRunConnectionTest:
    """`run_connection_test` records what it found and never raises - a failed test is a
    status to read, not an exception for the caller to handle."""

    def _testable_provider(self):
        """A provider with exactly one model of its own type to test against.

        The seeded global rows are cleared first: the suite runs with --reuse-db, so a test
        that leans on them is order-dependent.
        """
        provider = LlmProviderFactory()
        LlmProviderModel.objects.filter(type=provider.type).delete()
        LlmProviderModelFactory(team=provider.team, type=provider.type, name="gpt-4o-mini")
        return provider

    def test_a_pass_is_recorded_against_the_config_it_tested(self):
        provider = self._testable_provider()
        with mock.patch.object(LlmProvider, "invoke_test_model"):
            status = provider.run_connection_test()

        provider.refresh_from_db()
        assert status.state == "ok"
        recorded = provider.extra_data[EXTRA_DATA_KEY]
        assert recorded["outcome"] == "ok"
        assert recorded["fingerprint"] == provider.config_fingerprint
        assert recorded["tested_at"]

    def test_recording_a_result_leaves_the_rest_of_the_bag_alone(self):
        """extra_data is a general bag - whatever else is stored beside the test result has
        to survive the next test."""
        provider = self._testable_provider()
        provider.extra_data = {"something_else": "keep me"}
        provider.save()

        with mock.patch.object(LlmProvider, "invoke_test_model"):
            provider.run_connection_test()

        provider.refresh_from_db()
        assert provider.extra_data["something_else"] == "keep me"
        assert provider.extra_data[EXTRA_DATA_KEY]["outcome"] == "ok"

    def test_a_failure_is_recorded_rather_than_raised(self):
        provider = self._testable_provider()
        exc = Exception("invalid api key")
        exc.status_code = 401
        with mock.patch.object(LlmProvider, "invoke_test_model", side_effect=exc):
            status = provider.run_connection_test()

        assert status.label == "Verification failed"
        assert status.title == "Authentication failed"

    def test_a_rate_limit_is_not_reported_as_a_credentials_problem(self):
        """Regression: a 429 is technically a 4xx, but "check your credentials" is the wrong
        thing to tell someone who was just throttled."""
        provider = self._testable_provider()
        exc = Exception("rate limited")
        exc.status_code = 429
        with mock.patch.object(LlmProvider, "invoke_test_model", side_effect=exc):
            status = provider.run_connection_test()

        assert status.label == "Couldn't verify"
        assert not status.is_failure

    def test_no_configured_model_is_its_own_state(self):
        """Nothing was verified, but nothing is wrong either - the next step is to add a
        model, not to check the credentials."""
        provider = LlmProviderFactory()
        LlmProviderModel.objects.filter(type=provider.type).delete()

        status = provider.run_connection_test()

        assert status.label == "Can't verify"
        assert provider.extra_data[EXTRA_DATA_KEY]["outcome"] == "no_model"

    def test_an_untestable_provider_type_says_so(self):
        """Voyage AI's lack of chat support is inherent to the type, not a problem to fix."""
        provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))

        status = provider.run_connection_test()

        assert status.label == "Not supported"

    def test_an_invalid_configuration_is_a_failure_not_a_setup_state(self):
        provider = self._testable_provider()
        with mock.patch.object(
            LlmProvider, "invoke_test_model", side_effect=ServiceProviderConfigError(provider.type, "bad config")
        ):
            status = provider.run_connection_test()

        assert status.is_failure
        assert status.title == "The saved configuration is incomplete"

    def test_editing_credentials_moves_the_badge_back_to_unverified(self):
        """The result was recorded against the old key, so it says nothing about the new one."""
        provider = self._testable_provider()
        with mock.patch.object(LlmProvider, "invoke_test_model"):
            provider.run_connection_test()
        assert provider.connection_status.state == "ok"

        provider.config = {**provider.config, "openai_api_key": "sk-a-different-key"}

        assert provider.connection_status.state == "changed"
        assert provider.connection_status.label == "Not verified"

    def test_renaming_leaves_a_passing_result_standing(self):
        """The name is not a credential, so a pass is still true after a rename."""
        provider = self._testable_provider()
        with mock.patch.object(LlmProvider, "invoke_test_model"):
            provider.run_connection_test()

        provider.name = "Renamed provider"
        provider.save()

        assert provider.connection_status.state == "ok"

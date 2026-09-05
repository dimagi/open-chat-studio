from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from field_audit.models import AuditAction

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
class TestRunConnectionTestHook:
    """Saving verifies credentials and reports what the provider said, without raising -
    a failed check must never cost the user the credentials they just entered."""

    def test_a_pass_is_silent(self):
        provider = LlmProviderFactory()
        with mock.patch.object(LlmProvider, "test_connection"):
            assert provider.run_connection_test_hook() == ([], "")

    def test_a_failure_reports_the_providers_own_error(self):
        """No categorising: the provider says why far more precisely than a status code."""
        provider = LlmProviderFactory()
        error = Exception("Incorrect API key provided: sk-p***lt")
        with mock.patch.object(LlmProvider, "test_connection", side_effect=error):
            warnings, detail = provider.run_connection_test_hook()

        # The flash message stays short; the provider's own words go on the page.
        assert len(warnings) == 1
        assert "could not be verified" in warnings[0]
        assert "sk-p***lt" not in warnings[0]
        assert "Incorrect API key provided: sk-p***lt" in detail
        assert "Exception" in detail

    def test_a_long_provider_error_is_truncated(self):
        """A provider can return a response of any size, and this is rendered on the page."""
        provider = LlmProviderFactory()
        with mock.patch.object(LlmProvider, "test_connection", side_effect=Exception("x" * 5000)):
            _warnings, detail = provider.run_connection_test_hook()

        assert len(detail) < 2100
        assert detail.endswith("…")

    def test_no_configured_model_points_at_the_models_tab(self):
        """Nothing was verified, but nothing is wrong either - the next step is to add a
        model, not to check the credentials."""
        provider = LlmProviderFactory()
        with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
            warnings, detail = provider.run_connection_test_hook()

        assert len(warnings) == 1
        assert "no models configured" in warnings[0].lower()
        assert "Models tab" in warnings[0]
        # Nothing was sent, so there is no provider response to show.
        assert detail == ""

    def test_an_untestable_provider_type_is_silent(self):
        """Voyage AI's lack of chat support is inherent to the type, not an actionable problem."""
        provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))
        with mock.patch.object(
            LlmProvider, "test_connection", side_effect=ConnectionTestNotSupportedError(provider.type)
        ):
            assert provider.run_connection_test_hook() == ([], "")

    def test_an_invalid_configuration_is_reported_like_any_other_failure(self):
        """A genuinely invalid configuration is not the same as an unsupported provider type
        or a missing model: it is a real, actionable problem, not setup noise to swallow."""
        provider = LlmProviderFactory()
        error = ServiceProviderConfigError(provider.type, "invalid base_url")
        with mock.patch.object(LlmProvider, "test_connection", side_effect=error):
            warnings, detail = provider.run_connection_test_hook()

        assert len(warnings) == 1
        assert "invalid base_url" in detail


@pytest.mark.django_db()
class TestCredentialsVerifiedFlag:
    """`extra_data["verified_credentials"]` exists to answer one question: should the next
    save verify these credentials? A provider that has never passed a check keeps saying yes,
    so a failed check stays retryable without the user having to edit a credential to force it.
    """

    def test_a_provider_starts_unverified(self):
        """Nothing has been checked yet, so the first save has to check."""
        assert LlmProviderFactory().credentials_verified is False

    @pytest.mark.parametrize(
        ("extra_data", "expected"),
        [
            pytest.param(None, False, id="column-null"),
            pytest.param({}, False, id="key-missing"),
            pytest.param({"verified_credentials": False}, False, id="key-false"),
            pytest.param({"verified_credentials": True}, True, id="key-true"),
        ],
    )
    def test_only_a_true_flag_counts_as_verified(self, extra_data, expected):
        """A missing key and a stored False mean the same thing - never verified, and
        verified-then-failed both need the same next save. NULL is what a row inserted by
        the previous release, before the column existed in its model, leaves behind.
        """
        assert LlmProviderFactory(extra_data=extra_data).credentials_verified is expected

    def test_a_null_column_takes_a_recorded_result(self):
        """The row the previous release inserted has to survive its first check."""
        provider = LlmProviderFactory(extra_data=None)
        with mock.patch.object(LlmProvider, "test_connection"):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.extra_data == {"verified_credentials": True}
        assert provider.verification_error == ""

    def test_a_pass_records_the_credentials_as_verified(self):
        provider = LlmProviderFactory()
        with mock.patch.object(LlmProvider, "test_connection"):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.credentials_verified is True

    def test_a_failure_records_the_credentials_as_unverified(self):
        """Written rather than left missing: the next save must retry, and a stored False
        says the check ran and the provider rejected them."""
        provider = LlmProviderFactory(extra_data={"verified_credentials": True})
        with mock.patch.object(LlmProvider, "test_connection", side_effect=Exception("401")):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.extra_data["verified_credentials"] is False

    def test_no_configured_model_leaves_the_credentials_unverified(self):
        """Nothing reached the provider, so nothing is verified - and the next save retries
        once a model exists."""
        provider = LlmProviderFactory()
        with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.credentials_verified is False

    def test_an_untestable_provider_type_records_nothing(self):
        """Voyage AI has no check to pass, so there is no verification state to keep - an
        empty bag is what "this question does not apply" looks like."""
        provider = LlmProviderFactory(type=str(LlmProviderTypes.voyage))
        provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.extra_data == {}

    def test_a_failure_stores_the_provider_response_beside_the_flag(self):
        """Stored, not flashed: coming back to the page later has to still say why the
        credentials sitting in the form were rejected."""
        provider = LlmProviderFactory()
        error = Exception("Error code: 401 - Incorrect API key provided: sk-p***lt")
        with mock.patch.object(LlmProvider, "test_connection", side_effect=error):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert "Incorrect API key provided: sk-p***lt" in provider.verification_error

    def test_a_pass_clears_a_previously_stored_response(self):
        """The stored response describes the current credentials; once they pass there is
        nothing left to explain."""
        provider = LlmProviderFactory(
            extra_data={"verified_credentials": False, "verification_error": "Exception: 401"}
        )
        with mock.patch.object(LlmProvider, "test_connection"):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.verification_error == ""
        assert "verification_error" not in provider.extra_data

    def test_no_configured_model_stores_no_response(self):
        """Nothing was sent, so there is no provider response - and an older one describes a
        check that is no longer the most recent."""
        provider = LlmProviderFactory(
            extra_data={"verified_credentials": False, "verification_error": "Exception: 401"}
        )
        with mock.patch.object(LlmProvider, "test_connection", side_effect=NoTestableModelError(provider.type)):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.verification_error == ""

    def test_a_write_that_landed_during_the_check_is_not_clobbered(self):
        """The check makes a multi-second external call, so extra_data can change under it.
        The outcome has to merge into the row as it stands, not the copy loaded before."""
        provider = LlmProviderFactory(extra_data={})
        stale = LlmProvider.objects.get(pk=provider.pk)
        LlmProvider.objects.filter(pk=provider.pk).update(
            extra_data={"something_else": "written meanwhile"}, audit_action=AuditAction.AUDIT
        )

        with mock.patch.object(LlmProvider, "test_connection"):
            stale.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.extra_data == {"something_else": "written meanwhile", "verified_credentials": True}

    def test_recording_the_flag_leaves_other_extra_data_alone(self):
        """extra_data is a general bag; a retest must not drop what is stored beside it."""
        provider = LlmProviderFactory(extra_data={"something_else": "keep me"})
        with mock.patch.object(LlmProvider, "test_connection"):
            provider.run_connection_test_hook()

        provider.refresh_from_db()
        assert provider.extra_data == {"something_else": "keep me", "verified_credentials": True}

    @pytest.mark.parametrize(
        ("provider_type", "expected"),
        [
            pytest.param(LlmProviderTypes.openai, True, id="openai"),
            pytest.param(LlmProviderTypes.anthropic, True, id="anthropic"),
            pytest.param(LlmProviderTypes.voyage, False, id="voyage"),
        ],
    )
    def test_supports_connection_test_by_provider_type(self, provider_type, expected):
        assert LlmProviderFactory(type=str(provider_type)).supports_connection_test is expected

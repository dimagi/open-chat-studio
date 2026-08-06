from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.pipelines.models import Pipeline
from apps.pipelines.tests.utils import content_flow_node
from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _make_pipeline_referencing(llm_provider_model, llm_provider=None):
    params = {
        "llm_provider_model_id": str(llm_provider_model.id),
        "prompt": "You are a helpful assistant",
    }
    if llm_provider is not None:
        params["llm_provider_id"] = str(llm_provider.id)
    pipeline: Pipeline = PipelineFactory()  # ty: ignore[invalid-assignment]
    node_data = {node.flow_id: None for node in pipeline.node_set.all()}
    node_data["1"] = content_flow_node("1", "LLMResponseWithPrompt", label="LLM", params=params)
    pipeline.update_nodes_from_data(node_data)
    return pipeline


@pytest.mark.django_db()
class TestRemoveDeprecatedModelsCommand:
    def test_no_deleted_models(self, capsys):
        with patch("apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS", []):
            call_command("remove_deprecated_models", force=True)
        assert "No deleted models" in capsys.readouterr().out

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_deletes_model_and_nulls_pipeline_reference(self, mock_notify):
        """Without a replacement, pipeline node references are set to None."""
        model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        pipeline = _make_pipeline_referencing(model)
        node = pipeline.node_set.get(type="LLMResponseWithPrompt")

        deleted_models = [("openai", "gpt-4-old")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        assert not LlmProviderModel.objects.filter(id=model.id).exists()
        node.refresh_from_db()
        assert node.params.get("llm_provider_model_id") is None

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_deletes_model_and_migrates_pipeline_reference_to_replacement(self, mock_notify):
        """With a replacement, pipeline node references are updated to the replacement model."""
        old_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        replacement_model = LlmProviderModelFactory(team=None, type="openai", name="test-replacement-model")
        pipeline = _make_pipeline_referencing(old_model)
        node = pipeline.node_set.get(type="LLMResponseWithPrompt")

        deleted_models = [("openai", "gpt-4-old", "test-replacement-model")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        assert not LlmProviderModel.objects.filter(id=old_model.id).exists()
        node.refresh_from_db()
        assert node.params["llm_provider_model_id"] == replacement_model.id
        assert node.llm_provider_model_id == replacement_model.id

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_stale_deleted_provider_in_params_not_resurrected(self, mock_notify):
        """Regression: a node whose params still reference an already-deleted LlmProvider must
        not have that dangling id re-derived into the llm_provider_id FK column when the node is
        touched. Resurrecting it violated the deferred FK constraint at commit on prod deploy."""
        model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        provider = LlmProviderFactory()
        provider_id = provider.id
        pipeline = _make_pipeline_referencing(model, llm_provider=provider)
        node = pipeline.node_set.get(type="LLMResponseWithPrompt")
        assert node.llm_provider_id == provider_id

        # Delete the provider: SET_NULL nulls the FK column but the stale id lingers in params.
        provider.delete()
        node.refresh_from_db()
        assert node.llm_provider_id is None
        assert str(node.params["llm_provider_id"]) == str(provider_id)

        deleted_models = [("openai", "gpt-4-old")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        node.refresh_from_db()
        assert node.llm_provider_id is None

    @pytest.mark.parametrize(
        "replacement_name",
        [pytest.param("test-replacement-model", id="with-replacement"), pytest.param(None, id="without-replacement")],
    )
    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_repoints_evaluators(self, mock_notify, replacement_name):
        """The evaluator FK moves off the model, so the delete pre-check no longer blocks."""
        old_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        replacement_model = (
            LlmProviderModelFactory(team=None, type="openai", name=replacement_name) if replacement_name else None
        )
        evaluator = EvaluatorFactory.create(llm_provider_model=old_model)

        deleted_models = [("openai", "gpt-4-old", replacement_name) if replacement_name else ("openai", "gpt-4-old")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        assert not LlmProviderModel.objects.filter(id=old_model.id).exists()
        evaluator.refresh_from_db()
        assert evaluator.llm_provider_model_id == (replacement_model.id if replacement_model else None)

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_notifies_affected_team(self, mock_notify):
        """Affected teams receive a deleted_model_notification, not an email."""
        old_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        LlmProviderModelFactory(team=None, type="openai", name="test-replacement-model")
        pipeline = _make_pipeline_referencing(old_model)
        experiment = ExperimentFactory(pipeline=pipeline)

        deleted_models = [("openai", "gpt-4-old", "test-replacement-model")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["team"] == experiment.team
        assert kwargs["model_name"] == "openai/gpt-4-old"
        assert kwargs["replacement_model_name"] == "test-replacement-model"
        assert experiment.name in kwargs["affected"].chatbots

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_notifies_the_team_about_affected_evaluators(self, mock_notify):
        """Collected before the repoint moves the FK, or the evaluator goes unreported."""
        old_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")
        evaluator = EvaluatorFactory.create(llm_provider_model=old_model)

        deleted_models = [("openai", "gpt-4-old")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", force=True)

        kwargs = mock_notify.call_args.kwargs
        assert kwargs["team"] == evaluator.team
        assert evaluator.name in kwargs["affected"].evaluators

    @patch("apps.data_migrations.management.commands.remove_deprecated_models.deleted_model_notification")
    def test_dry_run_does_not_delete_or_notify(self, mock_notify):
        model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4-old")

        deleted_models = [("openai", "gpt-4-old")]
        with patch(
            "apps.data_migrations.management.commands.remove_deprecated_models.DELETED_MODELS",
            deleted_models,
        ):
            call_command("remove_deprecated_models", dry_run=True)

        assert LlmProviderModel.objects.filter(id=model.id).exists()
        mock_notify.assert_not_called()

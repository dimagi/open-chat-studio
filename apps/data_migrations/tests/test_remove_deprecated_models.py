from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory


def _make_pipeline_referencing(llm_provider_model):
    pipeline = PipelineFactory()
    pipeline.data["nodes"].append(
        {
            "id": "1",
            "data": {
                "id": "1",
                "label": "LLM",
                "type": "LLMResponseWithPrompt",
                "params": {
                    "llm_provider_model_id": str(llm_provider_model.id),
                    "prompt": "You are a helpful assistant",
                },
            },
        }
    )
    pipeline.update_nodes_from_data()
    pipeline.save()
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
        assert experiment.name in kwargs["affected_chatbots"]

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

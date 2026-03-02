from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.service_providers.llm_service.default_models import Model
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory

FAKE_DEPRECATED_MODELS = {
    "openai": [
        Model("gpt-4", 8192, deprecated=True, replacement="gpt-4o"),
        Model("gpt-4o", 128000, is_default=True),
    ],
}


@pytest.mark.django_db()
class TestNotifyDeprecatedModelsCommand:
    def test_no_deprecated_models_with_replacement(self, capsys):
        """If no deprecated models have a replacement set, nothing happens."""
        no_deprecated = {"openai": [Model("gpt-4o", 128000, is_default=True)]}
        with patch(
            "apps.data_migrations.management.commands.notify_deprecated_models.DEFAULT_LLM_PROVIDER_MODELS",
            no_deprecated,
        ):
            call_command("notify_deprecated_models", force=True)
        captured = capsys.readouterr()
        assert "No deprecated models" in captured.out

    @patch("apps.data_migrations.management.commands.notify_deprecated_models.deprecated_model_notification")
    def test_notifies_affected_teams(self, mock_notify):
        """Teams with pipeline references to deprecated models are notified."""
        deprecated_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4", deprecated=True)
        pipeline = _make_pipeline_referencing(deprecated_model)
        experiment = ExperimentFactory(pipeline=pipeline)

        with patch(
            "apps.data_migrations.management.commands.notify_deprecated_models.DEFAULT_LLM_PROVIDER_MODELS",
            FAKE_DEPRECATED_MODELS,
        ):
            call_command("notify_deprecated_models", force=True)

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args.kwargs
        assert call_kwargs["team"] == experiment.team
        assert call_kwargs["model_name"] == "openai/gpt-4"
        assert call_kwargs["replacement_model_name"] == "gpt-4o"
        assert experiment.name in call_kwargs["affected_chatbots"]

    @patch("apps.data_migrations.management.commands.notify_deprecated_models.deprecated_model_notification")
    def test_dry_run_does_not_notify(self, mock_notify):
        """Dry run previews without sending notifications."""
        deprecated_model = LlmProviderModelFactory(team=None, type="openai", name="gpt-4", deprecated=True)
        _make_pipeline_referencing(deprecated_model)

        with patch(
            "apps.data_migrations.management.commands.notify_deprecated_models.DEFAULT_LLM_PROVIDER_MODELS",
            FAKE_DEPRECATED_MODELS,
        ):
            call_command("notify_deprecated_models", dry_run=True)

        mock_notify.assert_not_called()

    @patch("apps.data_migrations.management.commands.notify_deprecated_models.deprecated_model_notification")
    def test_skips_teams_with_no_active_references(self, mock_notify):
        """Teams with no active references to deprecated models are not notified."""
        LlmProviderModelFactory(team=None, type="openai", name="gpt-4", deprecated=True)

        with patch(
            "apps.data_migrations.management.commands.notify_deprecated_models.DEFAULT_LLM_PROVIDER_MODELS",
            FAKE_DEPRECATED_MODELS,
        ):
            call_command("notify_deprecated_models", force=True)

        mock_notify.assert_not_called()


def _make_pipeline_referencing(llm_provider_model):
    """Create a pipeline with a node referencing the given LlmProviderModel."""
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

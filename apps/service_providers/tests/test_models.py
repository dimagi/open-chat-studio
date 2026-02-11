import pytest
from django.core.exceptions import ValidationError

from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory()


@pytest.fixture()
def assistant():
    return OpenAiAssistantFactory()


@pytest.fixture()
def pipeline(llm_provider, llm_provider_model):
    pipeline = PipelineFactory()
    pipeline.data["nodes"].append(
        {
            "id": "1",
            "data": {
                "id": "1",
                "label": "LLM",
                "type": "LLMResponseWithPrompt",
                "params": {
                    "llm_provider_id": str(llm_provider.id),
                    "llm_provider_model_id": str(llm_provider_model.id),
                    "prompt": "You are a helpful assistant",
                },
            },
        }
    )
    pipeline.update_nodes_from_data()
    pipeline.save()
    return pipeline


class TestServiceProviderModel:
    @django_db_with_data()
    def test_provider_models_for_team_includes_global(self, llm_provider_model):
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

    @django_db_with_data()
    def test_cannot_delete_provider_models_with_associated_models(self, assistant):
        # llm provider models that are associated with another model cannot be deleted
        provider_model = assistant.llm_provider_model
        with pytest.raises(ValidationError):
            provider_model.delete()

    @django_db_with_data()
    def test_cannot_delete_provider_models_with_associated_pipeline(self, pipeline):
        node = pipeline.node_set.get(flow_id="1")
        provider_model = LlmProviderModel.objects.get(id=node.params["llm_provider_model_id"])
        with pytest.raises(ValidationError, match=pipeline.name):
            provider_model.delete()

    @django_db_with_data()
    def test_can_delete_unassociated_provider_models(self):
        # custom llm provider models that are not attached to experiments can be deleted
        llm_provider_model = LlmProviderModelFactory()
        llm_provider_model.delete()

    @django_db_with_data()
    def test_can_delete_unassociated_global_provider_models(self):
        # global provider models can be deleted
        global_llm_provider_model = LlmProviderModelFactory(team=None)
        global_llm_provider_model.delete()

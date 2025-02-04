import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.service_providers.models import LlmProviderModel
from apps.service_providers.views import matches_blocking_deletion_condition
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory
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
def experiment(llm_provider_model):
    return ExperimentFactory(team=llm_provider_model.team, llm_provider_model=llm_provider_model)


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

    @pytest.mark.parametrize("fixture_name", ["experiment", "assistant"])
    @django_db_with_data()
    def test_cannot_delete_provider_models_with_associated_models(self, request, fixture_name):
        associated_object = request.getfixturevalue(fixture_name)
        # llm provider models that are associated with another model cannot be deleted
        provider_model = associated_object.llm_provider_model
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

    @django_db_with_data()
    def test_experiment_uses_llm_provider_model_max_token_limit(self, experiment):
        assert experiment.max_token_limit == 8192

        experiment.llm_provider_model.max_token_limit = 100
        assert experiment.max_token_limit == 100

    @pytest.mark.parametrize(
        ("obj", "expected"),
        [
            (type("MockObj", (), {"working_version_id": None}), True),
            (type("MockObj", (), {"is_default_version": True}), True),
            (type("MockObj", (), {"working_version_id": 1, "is_default_version": False}), False),
        ],
    )
    def test_matches_blocking_deletion_condition(self, obj, expected):
        assert matches_blocking_deletion_condition(obj) == expected

    @pytest.mark.parametrize("fixture_name", ["experiment", "assistant"])
    @django_db_with_data()
    def test_delete_service_provider_with_associated_models(self, client, request, fixture_name):
        associated_object = request.getfixturevalue(fixture_name)
        service_config = associated_object.llm_provider_model

        related_objects = [
            ExperimentFactory(llm_provider_model=service_config)
            if fixture_name == "experiment"
            else OpenAiAssistantFactory(llm_provider_model=service_config)
        ]

        url = reverse(
            "service_providers:delete", args=[service_config.team.slug, service_config.type, service_config.pk]
        )
        response = client.delete(url)

        assert response.status_code == 400  # Assert deletion is blocked

    @pytest.mark.django_db()
    def test_delete_service_provider_without_associated_models(self, client):
        from apps.utils.factories.team import TeamWithUsersFactory

        team = TeamWithUsersFactory()
        user = team.members.first()
        client.force_login(user)
        service_provider = LlmProviderModelFactory()
        print(f"Team: {team}, User: {user}, Service Provider: {service_provider}")

        url = reverse(
            "service_providers:delete", args=[service_provider.team.slug, service_provider.type, service_provider.pk]
        )
        print(f"URL: {url}")

        response = client.delete(url)
        print(f"Response Status Code: {response.status_code}")
        print(f"Response Content: {response.content}")

        assert response.status_code == 200

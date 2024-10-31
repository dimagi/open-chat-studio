import pytest
from django.core.exceptions import ValidationError

from apps.service_providers.models import LlmProviderModel
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory()


@pytest.fixture()
def experiment(llm_provider_model):
    return ExperimentFactory(team=llm_provider_model.team, llm_provider_model=llm_provider_model)


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
    def test_cannot_delete_provider_models_with_experiments(self, experiment):
        # custom llm provider models that are not attached to experiments can be deleted
        llm_provider_model = LlmProviderModelFactory()
        llm_provider_model.delete()

        # llm provider models that are associated with an experiment cannot be deleted
        experiment_provider_model = experiment.llm_provider_model
        with pytest.raises(ValidationError):
            experiment_provider_model.delete()

        # global provider models can be deleted
        global_llm_provider_model = LlmProviderModelFactory(team=None)
        global_llm_provider_model.delete()

    @django_db_with_data()
    def test_experiment_uses_llm_provider_model_max_token_limit(self, experiment):
        assert experiment.max_token_limit == 8192

        experiment.llm_provider_model.max_token_limit = 100
        assert experiment.max_token_limit == 100

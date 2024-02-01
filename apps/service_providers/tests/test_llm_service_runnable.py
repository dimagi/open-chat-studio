import pytest

from apps.service_providers.llm_service import LlmService
from apps.service_providers.llm_service.wrapper import ExperimentRunnable
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.langchain import FakeLlm


class FakeLlmService(LlmService):
    def get_chat_model(self, llm_model: str, temperature: float):
        return FakeLlm(responses=["this is a test message"], token_counts=[30, 20, 10])


@pytest.fixture()
def experiment():
    experiment = ExperimentFactory()
    experiment.llm_provider.get_llm_service = lambda: FakeLlmService()
    return experiment


@pytest.mark.django_db
def test_experiment_runnable(experiment):
    runnable = ExperimentRunnable(experiment=experiment)
    print(runnable.invoke({"input": "hi"}))

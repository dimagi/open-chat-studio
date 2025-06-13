from typing import cast
from unittest import mock

import pytest

from apps.evaluations.evaluators import LlmEvaluator
from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.evaluations.tasks import run_evaluation_task
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluatorFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory(name="gpt-4o")


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_running_evaluator(get_llm_service, llm_provider, llm_provider_model):
    service = build_fake_llm_service(responses=[{"sentiment": "positive"}], token_counts=[30])
    get_llm_service.return_value = service
    prompt = "evaluate the sentiment of the following conversation"

    message_1 = "Hello, I'm upbeat and friendly"
    message_2 = "Hello, I'm sad and downtrodden"

    # Use the factory to create evaluation messages
    evaluation_message_1 = EvaluationMessageFactory(
        human_message_content=message_1, ai_message_content="Hello! I'm glad to hear that.", create_chat_messages=True
    )
    evaluation_message_2 = EvaluationMessageFactory(
        human_message_content=message_2, ai_message_content="I'm sorry to hear that.", create_chat_messages=True
    )

    llm_evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt=prompt + " {input}",
        output_schema={"sentiment": "the sentiment of the conversation"},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message_1, evaluation_message_2])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))

    evaluation_run = EvaluationRun.objects.create(team=evaluation_config.team, config=evaluation_config)

    with mock.patch("apps.evaluations.tasks.ProgressRecorder"):
        run_evaluation_task(evaluation_run.id)

    evaluation_run.refresh_from_db()
    results = evaluation_run.results.all()

    assert len(results) == 2

    assert evaluation_run.status == "completed"

    assert "result" in results[0].output
    assert "sentiment" in results[0].output["result"]
    assert results[0].output["result"]["sentiment"] == "positive"

from typing import cast
from unittest import mock

import pytest

from apps.evaluations.evaluators import LlmEvaluator
from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.evaluations.tasks import evaluate_single_message_task
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

    evaluation_message_1 = EvaluationMessageFactory(
        input={"content": message_1, "role": "human"},
        output={"content": "Hello! I'm glad to hear that.", "role": "ai"},
        create_chat_messages=True,
    )
    evaluation_message_2 = EvaluationMessageFactory(
        input={"content": message_2, "role": "human"},
        output={"content": "I'm sorry to hear that.", "role": "ai"},
        create_chat_messages=True,
    )

    llm_evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt=prompt + " {input.content}",
        output_schema={"sentiment": "the sentiment of the conversation"},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message_1, evaluation_message_2])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))

    evaluation_run = EvaluationRun.objects.create(team=evaluation_config.team, config=evaluation_config)

    for message in dataset.messages.all():
        evaluate_single_message_task(evaluation_run.id, [evaluator.id], message.id)

    evaluation_run.refresh_from_db()
    results = evaluation_run.results.all()

    assert len(results) == 2

    assert "result" in results[0].output
    assert "sentiment" in results[0].output["result"]
    assert results[0].output["result"]["sentiment"] == "positive"


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_context_variables_in_prompt(get_llm_service, llm_provider, llm_provider_model):
    service = build_fake_llm_service(responses=[{"evaluation": "context_variables_working"}], token_counts=[30])
    get_llm_service.return_value = service

    prompt = (
        "Evaluate conversation for user {context.user_name} in session {context.session_id} at "
        "{context.current_datetime}. Input: {input.content}"
    )

    evaluation_message_1 = EvaluationMessageFactory(
        input={"content": "Hello, I need help", "role": "human"},
        output={"content": "Sure, I can help you", "role": "ai"},
        context={
            "session_id": "test-session-123",
            "user_name": "John Doe",
            "current_datetime": "2023-12-01T10:30:00",
            "history": "Previous conversation history",
        },
        create_chat_messages=True,
    )

    # This message is missing the 'user_name' context variable
    evaluation_message_2 = EvaluationMessageFactory(
        input={"content": "Help me please", "role": "human"},
        output={"content": "Of course, I'll help", "role": "ai"},
        context={
            "session_id": "test-session-456",
            # "user_name": missing intentionally
            "current_datetime": "2023-12-01T11:00:00",
            "history": "Another conversation",
        },
        create_chat_messages=True,
    )

    llm_evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt=prompt,
        output_schema={"evaluation": "the evaluation result"},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message_1, evaluation_message_2])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))

    evaluation_run = EvaluationRun.objects.create(team=evaluation_config.team, config=evaluation_config)

    for message in dataset.messages.all():
        evaluate_single_message_task(evaluation_run.id, [evaluator.id], message.id)

    evaluation_run.refresh_from_db()
    results = evaluation_run.results.all()
    assert len(results) == 2

    result_1 = next(r for r in results if r.message.input["content"] == "Hello, I need help")
    result_2 = next(r for r in results if r.message.input["content"] == "Help me please")

    assert "result" in result_1.output
    assert "evaluation" in result_1.output["result"]
    assert result_1.output["result"]["evaluation"] == "context_variables_working"

    # Missing variables should pass without issues
    assert "result" in result_2.output
    assert "evaluation" in result_2.output["result"]
    assert result_2.output["result"]["evaluation"] == "context_variables_working"

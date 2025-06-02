from typing import cast
from unittest import mock

import pytest

from apps.evaluations.evaluators import EvaluatorResult, LlmEvaluator
from apps.evaluations.models import EvaluationConfig, EvaluationMessage
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
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
    service = build_fake_llm_service(responses=["hello"], token_counts=[30])
    get_llm_service.return_value = service
    prompt = "evaluate the sentiment of the following conversation"

    chat = ChatFactory()
    message_1 = "Hello, I'm upbeat and friendly"
    message_2 = "Hello, I'm sad and downtrodden"
    chat_message_1 = ChatMessageFactory(message_type="human", content=message_1, chat=chat)
    chat_message_2 = ChatMessageFactory(message_type="human", content=message_2, chat=chat)

    evaluation_message_1 = EvaluationMessage.from_chat_message(chat_message_1)
    evaluation_message_2 = EvaluationMessage.from_chat_message(chat_message_2)

    llm_evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt=prompt + " {input}",
        output_schema={"sentiment": "the sentiment of the conversation"},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message_1, evaluation_message_2])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))
    results = evaluation_config.run()
    assert (
        results[0].output
        == EvaluatorResult(
            result={"route": f"{prompt} human: {message_1.lower()}\nhuman: {message_2.lower()}"}
        ).model_dump_json()
    )

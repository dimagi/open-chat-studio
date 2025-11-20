from typing import cast
from unittest import mock

import pytest
from langchain_core.messages import AIMessage

from apps.evaluations.evaluators import LlmEvaluator
from apps.evaluations.field_definitions import ChoiceFieldDefinition, IntFieldDefinition, StringFieldDefinition
from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.evaluations.tasks import evaluate_single_message_task
from apps.evaluations.utils import schema_to_pydantic_model
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
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "DynamicModel",
                "args": {"sentiment": "positive"},
                "id": "call_123",
            }
        ],
    )
    service = build_fake_llm_service(responses=[response], token_counts=[30])
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
        output_schema={"sentiment": {"type": "string", "description": "the sentiment of the conversation"}},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message_1, evaluation_message_2])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))

    evaluation_run = EvaluationRun.objects.create(team=evaluation_config.team, config=evaluation_config)

    for message in dataset.messages.all().order_by("created_at"):
        evaluate_single_message_task(evaluation_run.id, [evaluator.id], message.id)

    evaluation_run.refresh_from_db()
    results = evaluation_run.results.all().order_by("created_at")

    assert len(results) == 2

    assert "result" in results[0].output
    assert "sentiment" in results[0].output["result"]
    assert results[0].output["result"]["sentiment"] == "positive"

    assert results[0].output["message"] == {
        "input": {"content": "Hello, I'm upbeat and friendly", "role": "human"},
        "output": {"content": "Hello! I'm glad to hear that.", "role": "ai"},
        "context": {"current_datetime": "2023-01-01T00:00:00"},
        "history": [],
        "metadata": {},
    }
    assert results[1].output["message"] == {
        "input": {"content": "Hello, I'm sad and downtrodden", "role": "human"},
        "output": {"content": "I'm sorry to hear that.", "role": "ai"},
        "context": {"current_datetime": "2023-01-01T00:00:00"},
        "history": [],
        "metadata": {},
    }


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_context_variables_in_prompt(get_llm_service, llm_provider, llm_provider_model):
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "DynamicModel",
                "args": {"evaluation": "context_variables_working"},
                "id": "call_456",
            }
        ],
    )
    service = build_fake_llm_service(responses=[response], token_counts=[30])
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
        output_schema={"evaluation": {"type": "string", "description": "the evaluation result"}},
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


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_evaluator_with_missing_output(get_llm_service, llm_provider, llm_provider_model):
    """Test that the evaluator handles evaluation messages with missing AI output gracefully."""
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "DynamicModel",
                "args": {"assessment": "no response to evaluate"},
                "id": "call_789",
            }
        ],
    )
    service = build_fake_llm_service(responses=[response], token_counts=[30])
    get_llm_service.return_value = service

    prompt = "Evaluate the AI response to: {input.content}. Response: {output.content}"

    # Create an evaluation message with no AI output (failed to generate)
    evaluation_message = EvaluationMessageFactory(
        input={"content": "Hello, I need help", "role": "human"},
        output={},  # No AI response
        expected_output_chat_message=None,
        create_chat_messages=True,
    )

    llm_evaluator = LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt=prompt,
        output_schema={"assessment": {"type": "string", "description": "the assessment result"}},
    )
    evaluator = EvaluatorFactory(params=llm_evaluator.model_dump(), type="LlmEvaluator")
    dataset = EvaluationDatasetFactory(messages=[evaluation_message])
    evaluation_config = cast(EvaluationConfig, EvaluationConfigFactory(evaluators=[evaluator], dataset=dataset))

    evaluation_run = EvaluationRun.objects.create(team=evaluation_config.team, config=evaluation_config)

    evaluate_single_message_task(evaluation_run.id, [evaluator.id], evaluation_message.id)

    evaluation_run.refresh_from_db()
    results = evaluation_run.results.all()

    assert len(results) == 1
    # Should handle missing output gracefully
    assert results[0].output["message"]["output"] == {}
    assert results[0].output["message"]["input"] == {"content": "Hello, I need help", "role": "human"}
    assert "result" in results[0].output
    assert "assessment" in results[0].output["result"]


@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_evaluators_return_typed_pydantic_model(get_llm_service):
    output_schema = {
        "sentiment": StringFieldDefinition(type="string", description="the sentiment of the conversation"),
        "value": IntFieldDefinition(type="int", description="the value of the conversation"),
        "choices": ChoiceFieldDefinition(
            type="choice", description="the choice of the conversation", choices=["foo", "bar", "baz"]
        ),
    }
    output_model = schema_to_pydantic_model(output_schema)
    assert output_model.model_json_schema() == {
        "properties": {
            "sentiment": {"description": "the sentiment of the conversation", "title": "Sentiment", "type": "string"},
            "value": {"description": "the value of the conversation", "title": "Value", "type": "integer"},
            "choices": {
                "title": "Choices",
                "type": "string",
                "choices": ["foo", "bar", "baz"],
                "enum": ["foo", "bar", "baz"],
                "description": "the choice of the conversation",
            },
        },
        "required": ["sentiment", "value", "choices"],
        "title": "DynamicModel",
        "type": "object",
    }

    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "DynamicModel",
                "args": {
                    "sentiment": "positive",  # a string
                    "value": 5,  # an int
                    "choices": "foo",  # an enum member
                },
                "id": "call_123",
            }
        ],
    )
    service = build_fake_llm_service(responses=[response], token_counts=[30])
    get_llm_service.return_value = service

    llm = service.get_chat_model("gpt-4o")
    structured_llm = llm.with_structured_output(output_model)
    result = structured_llm.invoke("test prompt")
    assert isinstance(result, output_model), f"Expected {type(output_model)} instance, got {type(result)}"
    assert hasattr(result, "sentiment"), "Expected model to have 'sentiment' attribute"
    assert result.sentiment == "positive"

    assert hasattr(result, "value"), "Expected model to have 'value' attribute"
    assert result.value == 5

    assert hasattr(result, "choices"), "Expected model to have 'choices' attribute"
    assert result.choices == "foo"
    assert isinstance(result.choices, str)

    # Test that invalid enum values are rejected
    invalid_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "DynamicModel",
                "args": {
                    "sentiment": "positive",
                    "value": 5,
                    "choices": "invalid_choice",  # Not in ["foo", "bar", "baz"]
                },
                "id": "call_456",
            }
        ],
    )
    invalid_service = build_fake_llm_service(responses=[invalid_response], token_counts=[30])
    invalid_llm = invalid_service.get_chat_model("gpt-4o")
    invalid_structured_llm = invalid_llm.with_structured_output(output_model)

    # Should raise validation error for invalid enum value
    with pytest.raises(ValueError, match="invalid_choice"):
        invalid_structured_llm.invoke("test prompt")

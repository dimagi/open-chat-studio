"""Cost tracking for evaluator (judge) LLM calls — see apps/evaluations/usage.py.

Judge calls never reach OCSTracer, so these rows come from the evaluator's own
recording path rather than trace finalisation.
"""

from decimal import Decimal
from typing import cast
from unittest import mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageRecord
from apps.evaluations.evaluators import LlmEvaluator
from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.evaluations.tasks import _usage_context_for, evaluate_message
from apps.evaluations.usage import EvaluatorUsageContext, track_evaluator_usage
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.tests.langchain import FakeLlmSimpleTokenCount, build_fake_llm_service

# A name with no seeded global PricingRule, so these tests own its rates.
_MODEL = "test-judge-model"
_PROVIDER = "openai"


class FakeJudgeLlm(FakeLlmSimpleTokenCount):
    """A fake that carries the two things cost tracking reads off a real chat model.

    `model_name` is what LangChain surfaces to callbacks as `ls_model_name`, and
    `metadata["ocs_provider_type"]` is the provider tag `LlmService.get_chat_model`
    stamps in production — `FakeLlmService` overrides `get_chat_model` rather than
    `_chat_model`, so it never stamps it and the tag has to be set here.
    """

    model_name: str = _MODEL


def _judge_service(input_tokens: int = 1000, output_tokens: int = 500):
    """Fake LLM service whose single response reports exact token usage."""
    response = AIMessage(
        content="",
        tool_calls=[{"name": "DynamicModel", "args": {"sentiment": "positive"}, "id": "call_123"}],
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    fake_llm = FakeJudgeLlm(responses=[response], metadata={"ocs_provider_type": _PROVIDER})
    return build_fake_llm_service(responses=[response], fake_llm=fake_llm)


def _seed_rules():
    for kind, unit_price in ((ServiceKind.LLM_INPUT, "0.00015"), (ServiceKind.LLM_OUTPUT, "0.00060")):
        PricingRule.objects.create(
            team=None,
            provider_type=_PROVIDER,
            model_name=_MODEL,
            service_kind=kind,
            unit_price=unit_price,
        )


def _emit_usage(collector: MetricsCollector, input_tokens: int = 1000, output_tokens: int = 500) -> None:
    """Drive one exact-usage LLM call through the collector's callbacks."""
    run_id = uuid4()
    collector.on_llm_start(
        {},
        ["judge prompt"],
        run_id=run_id,
        invocation_params={"model": _MODEL},
        metadata={"ocs_provider_type": _PROVIDER},
    )
    message = AIMessage(
        content="verdict",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    collector.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=message, text="verdict")]], llm_output=None),
        run_id=run_id,
    )


@pytest.fixture()
def llm_provider():
    return LlmProviderFactory.create()


@pytest.fixture()
def llm_provider_model():
    return LlmProviderModelFactory.create(name=_MODEL)


def _llm_evaluator(llm_provider, llm_provider_model) -> LlmEvaluator:
    return LlmEvaluator(
        llm_provider_id=llm_provider.id,
        llm_provider_model_id=llm_provider_model.id,
        prompt="Judge the sentiment of {input.content}",
        output_schema={"sentiment": {"type": "string", "description": "the sentiment"}},
    )


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_judge_call_writes_usage_records(get_llm_service, llm_provider, llm_provider_model):
    get_llm_service.return_value = _judge_service()
    _seed_rules()

    evaluator = EvaluatorFactory.create(
        params=_llm_evaluator(llm_provider, llm_provider_model).model_dump(), type="LlmEvaluator"
    )
    message = EvaluationMessageFactory.create(
        input={"content": "Hello, I'm upbeat", "role": "human"},
        output={"content": "Glad to hear it!", "role": "ai"},
        create_chat_messages=True,
    )
    dataset = EvaluationDatasetFactory.create(messages=[message])
    config = cast(EvaluationConfig, EvaluationConfigFactory.create(evaluators=[evaluator], dataset=dataset))
    run = EvaluationRun.objects.create(team=config.team, config=config)

    evaluate_message(run.id, [evaluator.id], message.id)

    rows = {row.service_kind: row for row in UsageRecord.objects.all()}
    assert set(rows) == {ServiceKind.LLM_INPUT, ServiceKind.LLM_OUTPUT}

    input_row = rows[ServiceKind.LLM_INPUT]
    assert input_row.team_id == run.team_id
    assert input_row.provider_type == _PROVIDER
    assert input_row.model_name == _MODEL
    assert input_row.quantity == 1000
    assert input_row.confidence == Confidence.EXACT
    # 1000 tokens / 1000 * $0.00015
    assert input_row.cost == Decimal("0.00015000")
    assert input_row.extra == {"source": "evaluation", "evaluation_run_id": run.id}
    # 500 tokens / 1000 * $0.00060
    assert rows[ServiceKind.LLM_OUTPUT].cost == Decimal("0.00030000")
    # No generation experiment on this run, and judge calls never carry a trace.
    assert all(row.trace_id is None and row.experiment_id is None and row.session_id is None for row in rows.values())


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_evaluator_run_without_context_records_nothing(get_llm_service, llm_provider, llm_provider_model):
    """An evaluator invoked outside a run (no attribution to bill) records nothing."""
    get_llm_service.return_value = _judge_service()
    _seed_rules()
    message = EvaluationMessageFactory.create(
        input={"content": "Hello", "role": "human"},
        output={"content": "Hi", "role": "ai"},
        create_chat_messages=True,
    )

    result = _llm_evaluator(llm_provider, llm_provider_model).run(message, "")

    assert result.result == {"sentiment": "positive"}
    assert UsageRecord.objects.count() == 0


@pytest.mark.django_db()
def test_usage_context_links_generation_experiment_working_version():
    """Judge spend lands on the same chatbot as the bot generation it scores, which
    OCSTracer attributes to the working version rather than the version snapshot."""
    config = cast(EvaluationConfig, EvaluationConfigFactory.create())
    working = ExperimentFactory.create(team=config.team)
    version = ExperimentFactory.create(team=config.team, working_version=working, version_number=2)
    run = EvaluationRun.objects.create(team=config.team, config=config, generation_experiment=version)

    context = _usage_context_for(run, session_id=42)

    assert context == EvaluatorUsageContext(
        team_id=config.team_id,
        evaluation_run_id=run.id,
        experiment_id=working.id,
        session_id=42,
    )


@pytest.mark.django_db()
def test_usage_recorded_when_judge_call_fails(team):
    """Tokens are billed whether or not the response was usable, so a failing call
    still writes its usage (the recording runs from a `finally`)."""
    context = EvaluatorUsageContext(team_id=team.id, evaluation_run_id=7)

    def judge_and_fail():
        with track_evaluator_usage(context) as callbacks:
            _emit_usage(callbacks[0])
            raise RuntimeError("judge exploded")

    with pytest.raises(RuntimeError, match="judge exploded"):
        judge_and_fail()

    row = UsageRecord.objects.get(service_kind=ServiceKind.LLM_INPUT)
    assert row.quantity == 1000
    assert row.extra["evaluation_run_id"] == 7


@pytest.mark.django_db()
def test_recording_failure_does_not_break_the_evaluator(team):
    """A cost-tracking failure must not surface as an evaluation failure."""
    context = EvaluatorUsageContext(team_id=team.id, evaluation_run_id=7)

    with mock.patch("apps.evaluations.usage.record_usage_bulk", side_effect=RuntimeError("db down")):
        with track_evaluator_usage(context) as callbacks:
            _emit_usage(callbacks[0])

    assert UsageRecord.objects.count() == 0


def test_no_context_yields_no_callbacks():
    with track_evaluator_usage(None) as callbacks:
        assert callbacks == []

"""Cost tracking for the bot generation an evaluation run drives — see ADR-0049.

These rows are written by `UsageOnlyTracer` when the generation's trace closes, so they
land with no `Trace` to point at; what makes them evaluation spend is the `UsageContext`
the eval run hands the tracer, not the platform of the session it created.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone
from langchain_core.messages import AIMessage

from apps.cost_tracking.models import PricingRule, ServiceKind, UsageRecord, UsageSource
from apps.cost_tracking.services.reporting import cost_summary, costs_by_experiment, session_usage
from apps.evaluations.models import EvaluationRun
from apps.evaluations.tasks import run_bot_generation
from apps.experiments.models import ExperimentSession
from apps.pipelines.tests.utils import create_pipeline_model, end_node, llm_response_node, start_node
from apps.trace.models import Trace
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluationMessageFactory
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.langchain import FakeLlmSimpleTokenCount, build_fake_llm_service

# A name with no seeded global PricingRule, so this module owns its rates.
_MODEL = "test-generation-model"
_PROVIDER = "openai"


class FakeGenerationLlm(FakeLlmSimpleTokenCount):
    """Surfaces the model name cost tracking reads off a real chat model. The other half
    of the pricing key — the provider tag `LlmService.get_chat_model` normally stamps — is
    supplied per instance via `metadata`, since `FakeLlmService` overrides
    `get_chat_model` and so never stamps it.
    """

    model_name: str = _MODEL


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def llm_experiment(team, db):
    """A chatbot whose pipeline makes one LLM call."""
    experiment = ChatbotFactory.create(team=team)
    provider = LlmProviderFactory.create(team=team)
    provider_model = LlmProviderModelFactory.create(team=team, name=_MODEL)
    create_pipeline_model(
        [start_node(), llm_response_node(str(provider.id), str(provider_model.id)), end_node()],
        pipeline=experiment.pipeline,
    )
    experiment.pipeline.save()
    return experiment


@pytest.fixture()
def pricing():
    for kind, unit_price in ((ServiceKind.LLM_INPUT, "0.00015"), (ServiceKind.LLM_OUTPUT, "0.00060")):
        PricingRule.objects.create(
            team=None, provider_type=_PROVIDER, model_name=_MODEL, service_kind=kind, unit_price=unit_price
        )


def _generation_service(input_tokens: int = 1000, output_tokens: int = 500):
    response = AIMessage(
        content="Bot response",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    fake_llm = FakeGenerationLlm(responses=[response], metadata={"ocs_provider_type": _PROVIDER})
    return build_fake_llm_service(responses=None, fake_llm=fake_llm)


@pytest.fixture()
def evaluation_run(team, llm_experiment):
    config = EvaluationConfigFactory.create(team=team)
    return EvaluationRun.objects.create(team=team, config=config, generation_experiment=llm_experiment)


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_generation_spend_is_evaluation_spend(get_llm_service, llm_experiment, team, pricing, evaluation_run):
    """The run exercised the chatbot, but nobody asked it to serve that traffic, so the
    spend is the team's and not the chatbot's or the conversation's."""
    get_llm_service.return_value = _generation_service()
    message = EvaluationMessageFactory.create(input={"content": "Hello", "role": "human"})

    session_id, response = run_bot_generation(team, message, llm_experiment, evaluation_run=evaluation_run)

    assert response == "Bot response"
    rows = {row.service_kind: row for row in UsageRecord.objects.filter(team=team)}
    assert set(rows) == {ServiceKind.LLM_INPUT, ServiceKind.LLM_OUTPUT}
    assert {row.source for row in rows.values()} == {UsageSource.EVALUATION}
    # 1000 tokens / 1000 * $0.00015
    assert rows[ServiceKind.LLM_INPUT].cost == Decimal("0.00015000")
    # Attributed like the judge calls scoring the same run, so both halves group together.
    assert all(row.evaluation_config_id == evaluation_run.config_id for row in rows.values())
    assert all(row.extra["evaluation_run_id"] == evaluation_run.id for row in rows.values())
    assert all(row.experiment_id == llm_experiment.id and row.session_id == session_id for row in rows.values())


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_generation_leaves_no_trace(get_llm_service, llm_experiment, team, pricing, evaluation_run):
    """Billing an eval run must not resurrect the Trace rows eval channels deliberately
    don't write — the spend is recorded with no trace to point at."""
    get_llm_service.return_value = _generation_service()
    message = EvaluationMessageFactory.create(input={"content": "Hello", "role": "human"})

    run_bot_generation(team, message, llm_experiment, evaluation_run=evaluation_run)

    assert Trace.objects.count() == 0
    assert UsageRecord.objects.filter(team=team, trace__isnull=True).count() == 2


@pytest.mark.django_db()
@mock.patch("apps.service_providers.models.LlmProvider.get_llm_service")
def test_reads_bill_the_team_but_not_the_chatbot(get_llm_service, llm_experiment, team, pricing, evaluation_run):
    """The outcome anyone watching the numbers sees: the team pays for the run, the
    chatbot it exercised does not."""
    get_llm_service.return_value = _generation_service()
    message = EvaluationMessageFactory.create(input={"content": "Hello", "role": "human"})
    run_bot_generation(team, message, llm_experiment, evaluation_run=evaluation_run)
    window = {"start": timezone.now() - timedelta(days=1), "end": timezone.now() + timedelta(days=1)}

    # 1000 input tokens @ $0.00015/1K + 500 output @ $0.00060/1K
    assert cost_summary(team, **window).total_cost == Decimal("0.00045000")
    assert costs_by_experiment(team, **window) == {}
    assert session_usage(ExperimentSession.objects.get(experiment=llm_experiment)).total_cost == Decimal(0)

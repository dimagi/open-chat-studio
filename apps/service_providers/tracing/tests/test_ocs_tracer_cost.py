"""Integration tests for the cost-tracking drain hooked into OCSTracer."""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from apps.cost_tracking.models import PricingRule, ServiceKind, UsageRecord
from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.ocs_tracer import OCSTracer
from apps.teams.models import Flag
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture(autouse=True)
def _clear_waffle_cache():
    """Waffle caches the (flag_name -> team_ids) set in Redis. The cache
    isn't transactional and persists across tests, so stale state from
    a prior test that read the flag with no teams attached would mask the
    team membership added here. Clear before and after each test.
    """
    cache.clear()
    yield
    cache.clear()


def _llm_result(input_tokens: int, output_tokens: int, model: str = "test-model") -> LLMResult:
    message = AIMessage(
        content="response",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=message, text="response")]], llm_output=None)


def _enable_flag_for(team) -> Flag:
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    return flag


def _seed_rule(provider: str, model: str, kind: ServiceKind, unit_price: str) -> PricingRule:
    return PricingRule.objects.create(
        team=None,
        provider_type=provider,
        model_name=model,
        service_kind=kind,
        unit_price=unit_price,
    )


# Unit tests: short-circuit cases for _record_costs (no DB writes happen)


class TestRecordCostsShortCircuits:
    """Each branch where `_record_costs` should bail without touching the recorder."""

    def test_short_circuits_when_flag_off(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        tracer.cost_tracking_enabled = False
        with patch("apps.cost_tracking.services.recorder.record_usage_bulk") as recorder:
            tracer._record_costs()
            recorder.assert_not_called()

    def test_short_circuits_when_collector_missing(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        tracer.cost_tracking_enabled = True
        tracer.metrics_collector = None
        with patch("apps.cost_tracking.services.recorder.record_usage_bulk") as recorder:
            tracer._record_costs()
            recorder.assert_not_called()

    def test_short_circuits_when_no_events(self, experiment):
        """A trace with no LLM calls (`iter_cost_events` yields nothing) doesn't call the recorder."""
        from apps.service_providers.tracing.metrics import MetricsCollector  # noqa: PLC0415 - test scope

        tracer = OCSTracer(experiment, experiment.team_id)
        tracer.cost_tracking_enabled = True
        tracer.metrics_collector = MetricsCollector(start_time=0.0)
        tracer.trace_record = object()  # only needs to be truthy here
        with patch("apps.cost_tracking.services.recorder.record_usage_bulk") as recorder:
            tracer._record_costs()
            recorder.assert_not_called()


# Integration tests: full trace -> UsageRecord row written


@pytest.mark.django_db()
class TestCostTrackingFlagGate:
    """`cost_tracking_enabled` reflects the team-scoped Waffle flag at trace entry."""

    def test_disabled_by_default(self, experiment):
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        with tracer.trace(trace_context=TraceContext(id=uuid4(), name="t"), session=session):
            assert tracer.cost_tracking_enabled is False

    def test_enabled_when_flag_active_for_team(self, experiment):
        _enable_flag_for(experiment.team)
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        with tracer.trace(trace_context=TraceContext(id=uuid4(), name="t"), session=session):
            assert tracer.cost_tracking_enabled is True

    def test_reset_between_traces(self, experiment):
        _enable_flag_for(experiment.team)
        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        with tracer.trace(trace_context=TraceContext(id=uuid4(), name="t"), session=session):
            pass
        # Outside the context, the per-trace state should be cleared.
        assert tracer.cost_tracking_enabled is False
        assert tracer.metrics_collector is None


@pytest.mark.django_db()
class TestCostRecordingEndToEnd:
    """Full path: trace context -> LLM callbacks -> finalisation writes UsageRecord rows."""

    def test_writes_usage_records_when_flag_on(self, experiment):
        _enable_flag_for(experiment.team)
        _seed_rule("openai", "test-model", ServiceKind.LLM_INPUT, "0.00015")
        _seed_rule("openai", "test-model", ServiceKind.LLM_OUTPUT, "0.00060")

        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        ctx = TraceContext(id=uuid4(), name="t")

        with tracer.trace(trace_context=ctx, session=session):
            tracer.metrics_collector.on_llm_start(
                {},
                ["hello world"],
                run_id=uuid4(),
                invocation_params={"model": "test-model"},
                metadata={"ocs_provider_type": "openai"},
            )
            tracer.metrics_collector.on_llm_end(_llm_result(1000, 500), run_id=uuid4())

        rows = UsageRecord.objects.filter(team=experiment.team).order_by("service_kind")
        assert rows.count() == 2
        by_kind = {r.service_kind: r for r in rows}
        # 1000 tokens / 1000 * $0.00015 = $0.00015
        assert by_kind[ServiceKind.LLM_INPUT].cost == Decimal("0.00015000")
        # 500 tokens / 1000 * $0.00060 = $0.00030
        assert by_kind[ServiceKind.LLM_OUTPUT].cost == Decimal("0.00030000")
        assert all(r.trace_id is not None for r in rows)
        assert all(r.session_id == session.id for r in rows)

    def test_writes_nothing_when_flag_off(self, experiment):
        _seed_rule("openai", "test-model", ServiceKind.LLM_INPUT, "0.00015")

        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        ctx = TraceContext(id=uuid4(), name="t")

        with tracer.trace(trace_context=ctx, session=session):
            tracer.metrics_collector.on_llm_start(
                {},
                ["hello world"],
                run_id=uuid4(),
                invocation_params={"model": "test-model"},
                metadata={"ocs_provider_type": "openai"},
            )
            tracer.metrics_collector.on_llm_end(_llm_result(1000, 500), run_id=uuid4())

        assert UsageRecord.objects.filter(team=experiment.team).count() == 0

    def test_trace_finalisation_continues_when_recorder_fails(self, experiment):
        """A cost-recording failure must not block trace_record.save() —
        outer try/except logs and continues."""
        _enable_flag_for(experiment.team)

        tracer = OCSTracer(experiment, experiment.team_id)
        session = ExperimentSessionFactory.create(experiment=experiment, team=experiment.team)
        ctx = TraceContext(id=uuid4(), name="t")

        with (
            patch(
                "apps.service_providers.tracing.ocs_tracer.OCSTracer._record_costs",
                side_effect=RuntimeError("simulated failure"),
            ),
            tracer.trace(trace_context=ctx, session=session) as _,
        ):
            tracer.metrics_collector.on_llm_start({}, ["prompt"], run_id=uuid4())

        # Trace itself should still be persisted with its end-state, even
        # though cost recording blew up inside _finalize_trace's try/except.
        # We check status (set to SUCCESS just before save()) rather than
        # duration, which can round to 0ms on fast machines.
        from apps.trace.models import Trace, TraceStatus  # noqa: PLC0415 - test scope

        trace_row = Trace.objects.get(team_id=experiment.team_id, trace_id=ctx.id)
        assert trace_row.status == TraceStatus.SUCCESS

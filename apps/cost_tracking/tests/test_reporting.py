"""Tests for the cost-tracking read path (`services/reporting.py`)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageSource
from apps.cost_tracking.services.reporting import (
    CostFilters,
    chatbot_usage_summary,
    cost_summary,
    cost_timeseries,
    cost_total,
    costs_by_experiment,
    coverage_gaps,
    session_usage,
    token_counts,
    trace_token_usage,
    usage_timeseries,
)
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
# The 30-day window most tests read over, named where a test needs it inside a lambda.
_START = _NOW - timedelta(days=30)


def _usage(team, *, cost, when, **kwargs):
    """Thin wrapper around UsageRecordFactory that coerces `cost` to Decimal
    and forwards optional kwargs (confidence, experiment, session, quantity).
    """
    return UsageRecordFactory.create(team=team, cost=Decimal(str(cost)), at=when, **kwargs)


@pytest.mark.django_db()
class TestCostSummary:
    """Period rollup, prior-period delta, confidence split, team scoping."""

    def test_sums_period_records_excluding_outside(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))
        _usage(team, cost="0.50", when=_NOW - timedelta(days=2))
        _usage(team, cost="9.99", when=_NOW - timedelta(days=40))  # outside window

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.total_cost == Decimal("1.50")

    def test_previous_period_uses_equal_length_prior_window(self):
        team = TeamFactory.create()
        _usage(team, cost="2.00", when=_NOW - timedelta(days=1))
        _usage(team, cost="3.00", when=_NOW - timedelta(days=45))

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.total_cost == Decimal("2.00")
        assert summary.previous_period_cost == Decimal("3.00")

    @pytest.mark.parametrize(
        ("total", "previous", "expected"),
        [
            pytest.param("0.00", "0.00", None, id="both-zero"),
            pytest.param("5.00", "0.00", None, id="previous-zero"),
            pytest.param("2.00", "1.00", 100.0, id="doubled"),
            pytest.param("0.50", "1.00", -50.0, id="halved"),
        ],
    )
    def test_delta_pct(self, total, previous, expected):
        team = TeamFactory.create()
        if Decimal(total) > 0:
            _usage(team, cost=total, when=_NOW - timedelta(days=1))
        if Decimal(previous) > 0:
            _usage(team, cost=previous, when=_NOW - timedelta(days=45))

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.delta_pct == expected

    def test_team_scoped(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1))

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.total_cost == Decimal("1.00")

    def test_splits_cost_by_confidence(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), confidence=Confidence.EXACT)
        _usage(team, cost="0.20", when=_NOW - timedelta(days=2), confidence=Confidence.ESTIMATED)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=3), confidence=Confidence.UNKNOWN)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=4), confidence=Confidence.UNKNOWN)

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.exact_cost == Decimal("1.00")
        assert summary.estimated_cost == Decimal("0.20")
        assert summary.estimated_call_count == 1
        assert summary.unknown_call_count == 2

    def test_estimated_call_count_counts_zero_cost_rows(self):
        """`estimated_call_count` is a row count, not derived from `estimated_cost` - a $0
        estimated row (e.g. a zero-priced model) must still register as estimated usage, since
        a Decimal 0 is falsy and would otherwise be indistinguishable from no estimated usage
        at all when a caller checks truthiness of the cost instead."""
        team = TeamFactory.create()
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), confidence=Confidence.ESTIMATED)

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.estimated_cost == Decimal(0)
        assert summary.estimated_call_count == 1

    def test_counts_unpriced_rows_excluding_unknown(self):
        """EXACT/ESTIMATED rows that the resolver couldn't price (pricing_rule
        is NULL) feed `unpriced_call_count`. UNKNOWN-confidence rows are
        excluded because they have their own counter."""
        team = TeamFactory.create()
        rule = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="test-priced-model",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.0001",
        )
        # Two EXACT rows with no pricing rule - the lead's failure mode.
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), confidence=Confidence.EXACT)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=2), confidence=Confidence.EXACT)
        # One ESTIMATED row, also unpriced.
        _usage(team, cost="0.00", when=_NOW - timedelta(days=3), confidence=Confidence.ESTIMATED)
        # One UNKNOWN-confidence row, also unpriced - must NOT count here.
        _usage(team, cost="0.00", when=_NOW - timedelta(days=4), confidence=Confidence.UNKNOWN)
        # A priced EXACT row - must not count.
        _usage(team, cost="0.50", when=_NOW - timedelta(days=5), confidence=Confidence.EXACT, pricing_rule=rule)

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.unpriced_call_count == 3
        assert summary.unknown_call_count == 1

    def test_single_query_for_aggregate(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))

        with CaptureQueriesContext(connection) as ctx:
            cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        # Single aggregate over UsageRecord - no N+1.
        assert len(ctx.captured_queries) == 1


@pytest.mark.django_db()
class TestCostsByExperiment:
    """Per-experiment cost map feeding the Bot Performance table."""

    def test_single_query(self):
        team = TeamFactory.create()
        exp_a = ExperimentFactory.create(team=team, name="bot-a")
        exp_b = ExperimentFactory.create(team=team, name="bot-b")
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp_a)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=2), experiment=exp_b)

        with CaptureQueriesContext(connection) as ctx:
            costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW)

        # Single GROUP BY query — no N+1 per experiment.
        assert len(ctx.captured_queries) == 1

    def test_aggregates_per_experiment(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team, name="bot")
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=2), experiment=exp)

        costs = costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert costs == {exp.id: Decimal("3.00000000")}

    def test_excludes_records_with_null_experiment(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=None)

        assert costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW) == {}

    def test_excludes_records_outside_window(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        _usage(team, cost="9.99", when=_NOW - timedelta(days=40), experiment=exp)

        assert costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW) == {}

    def test_team_scoped(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        exp_other = ExperimentFactory.create(team=other)
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1), experiment=exp_other)

        assert costs_by_experiment(team, start=_NOW - timedelta(days=30), end=_NOW) == {}


@pytest.mark.django_db()
class TestChatbotUsageSummary:
    """Cost + session/message counts for one chatbot's usage widget (chatbot home page).
    Uses real `timezone.now()` rather than the frozen `_NOW` other classes use, since
    `ExperimentSession.created_at`/`ChatMessage.created_at` are `auto_now_add` and can't be
    backdated without the same post-generation trick `UsageRecordFactory.at` uses."""

    def _window(self):
        end = timezone.now()
        return end - timedelta(days=30), end

    def test_empty_when_no_activity(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        start, end = self._window()

        usage = chatbot_usage_summary(experiment, start=start, end=end)

        assert usage.cost.total_cost == Decimal(0)
        assert usage.sessions_count == 0
        assert usage.messages_count == 0

    def test_aggregates_cost_sessions_and_messages(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(experiment=experiment, team=team)
        UsageRecordFactory.create(team=team, experiment=experiment, session=session, cost=Decimal("1.50"))
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="hello")
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.SYSTEM, content="sys")
        start, end = self._window()

        usage = chatbot_usage_summary(experiment, start=start, end=end)

        assert usage.cost.total_cost == Decimal("1.50000000")
        assert usage.sessions_count == 1
        assert usage.messages_count == 2  # SYSTEM excluded - not a conversation turn

    def test_scoped_to_experiment(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        other_experiment = ExperimentFactory.create(team=team)
        session = ExperimentSessionFactory.create(experiment=experiment, team=team)
        other_session = ExperimentSessionFactory.create(experiment=other_experiment, team=team)
        UsageRecordFactory.create(team=team, experiment=experiment, session=session, cost=Decimal("1.00"))
        UsageRecordFactory.create(team=team, experiment=other_experiment, session=other_session, cost=Decimal("9.00"))
        start, end = self._window()

        usage = chatbot_usage_summary(experiment, start=start, end=end)

        assert usage.cost.total_cost == Decimal("1.00000000")
        assert usage.sessions_count == 1

    def test_excludes_evaluation_sessions(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        ExperimentSessionFactory.create(experiment=experiment, team=team, platform=ChannelPlatform.EVALUATIONS)
        start, end = self._window()

        usage = chatbot_usage_summary(experiment, start=start, end=end)

        assert usage.sessions_count == 0


@pytest.mark.django_db()
class TestSessionUsage:
    """Per-session, per-model cost/token breakdown and session scoping."""

    def test_empty_when_no_records(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        usage = session_usage(session)

        assert usage.total_cost == Decimal(0)
        assert usage.total_tokens == 0
        assert usage.by_model == []
        assert usage.has_unpriced is False
        assert usage.has_estimated is False
        assert usage.has_unknown is False

    def test_groups_by_model_with_total(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW, session=session, model_name="gpt-4o", quantity=100)
        _usage(team, cost="2.00", when=_NOW, session=session, model_name="gpt-4o", quantity=200)
        _usage(team, cost="0.50", when=_NOW, session=session, model_name="gpt-4o-mini", quantity=50)

        usage = session_usage(session)

        assert usage.total_cost == Decimal("3.50000000")
        assert usage.total_tokens == 350
        assert [(m.model_name, m.cost, m.tokens) for m in usage.by_model] == [
            ("gpt-4o", Decimal("3.00000000"), 300),
            ("gpt-4o-mini", Decimal("0.50000000"), 50),
        ]

    def test_scoped_to_session(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        other = ExperimentSessionFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW, session=session, model_name="gpt-4o")
        _usage(team, cost="9.00", when=_NOW, session=other, model_name="gpt-4o")

        usage = session_usage(session)

        assert usage.total_cost == Decimal("1.00000000")
        assert len(usage.by_model) == 1

    def test_no_pricing_data_when_all_rows_unpriced(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, session=session, model_name="gpt-4o", quantity=100)

        usage = session_usage(session)

        assert usage.total_cost == Decimal(0)
        assert usage.has_unpriced is True

    def test_flags_estimated_and_unknown_confidence(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, session=session, model_name="gpt-4o", confidence=Confidence.ESTIMATED)
        _usage(
            team,
            cost="0",
            when=_NOW,
            session=session,
            model_name="gpt-4o",
            quantity=None,
            confidence=Confidence.UNKNOWN,
        )

        usage = session_usage(session)

        assert usage.has_estimated is True
        assert usage.has_unknown is True
        row = usage.by_model[0]
        assert row.has_estimated is True
        assert row.has_unknown is True


@pytest.mark.django_db()
class TestTraceTokenUsage:
    """Per-trace token breakdown — the trace detail page's token card."""

    def test_empty_when_no_records(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)

        usage = trace_token_usage(trace)

        assert usage.by_model == []
        assert usage.total == 0
        assert usage.total_cost == Decimal(0)
        assert usage.has_unpriced is False
        assert usage.has_estimated is False
        assert usage.has_unknown is False

    def test_splits_input_and_output(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=1000, service_kind=ServiceKind.LLM_INPUT)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=500, service_kind=ServiceKind.LLM_OUTPUT)

        usage = trace_token_usage(trace)

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.total == 1500

    def test_cache_kinds_count_as_input(self):
        """The card's two-way split folds both cache buckets into input, so the headline
        total matches what the provider reported for the call."""
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=500, service_kind=ServiceKind.LLM_INPUT)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=300, service_kind=ServiceKind.LLM_CACHED_INPUT)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=200, service_kind=ServiceKind.LLM_CACHE_WRITE)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=400, service_kind=ServiceKind.LLM_OUTPUT)

        usage = trace_token_usage(trace)

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 400
        assert usage.total == 1400
        row = usage.by_model[0]
        assert (row.input_tokens, row.cached_input_tokens, row.cache_write_tokens) == (500, 300, 200)

    def test_groups_by_provider_and_model(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        for provider, model, kind, qty in [
            ("openai", "gpt-4o", ServiceKind.LLM_INPUT, 100),
            ("openai", "gpt-4o", ServiceKind.LLM_OUTPUT, 40),
            ("anthropic", "claude-haiku-4-5", ServiceKind.LLM_INPUT, 70),
        ]:
            _usage(
                team,
                cost="0",
                when=_NOW,
                trace=trace,
                provider_type=provider,
                model_name=model,
                service_kind=kind,
                quantity=qty,
            )

        usage = trace_token_usage(trace)

        assert [(m.provider_type, m.model_name, m.total_input_tokens, m.output_tokens) for m in usage.by_model] == [
            ("anthropic", "claude-haiku-4-5", 70, 0),
            ("openai", "gpt-4o", 100, 40),
        ]

    def test_scoped_to_trace(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        other = TraceFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, trace=trace, quantity=100, service_kind=ServiceKind.LLM_INPUT)
        _usage(team, cost="0", when=_NOW, trace=other, quantity=900, service_kind=ServiceKind.LLM_INPUT)
        _usage(team, cost="0", when=_NOW, quantity=900, service_kind=ServiceKind.LLM_INPUT)  # untraced

        assert trace_token_usage(trace).total == 100

    def test_unknown_confidence_rows_count_as_zero(self):
        """A call with no usage data is recorded with `quantity=None`; it must not
        blow up the sum or make the card claim tokens it doesn't have."""
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(
            team,
            cost="0",
            when=_NOW,
            trace=trace,
            quantity=None,
            confidence=Confidence.UNKNOWN,
            service_kind=ServiceKind.LLM_INPUT,
        )

        usage = trace_token_usage(trace)

        assert usage.total == 0
        assert len(usage.by_model) == 1

    def test_sums_cost_and_flags_fully_priced_rows(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        rule = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="test-priced-model",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00015",
        )
        _usage(
            team,
            cost="1.00",
            when=_NOW,
            trace=trace,
            model_name="test-priced-model",
            service_kind=ServiceKind.LLM_INPUT,
            quantity=100,
            pricing_rule=rule,
        )
        _usage(
            team,
            cost="0.50",
            when=_NOW,
            trace=trace,
            model_name="test-priced-model",
            service_kind=ServiceKind.LLM_OUTPUT,
            quantity=50,
            pricing_rule=rule,
        )

        usage = trace_token_usage(trace)

        assert usage.total_cost == Decimal("1.50000000")
        assert usage.has_unpriced is False
        assert usage.has_estimated is False
        assert usage.has_unknown is False
        row = usage.by_model[0]
        assert row.cost == Decimal("1.50000000")
        assert row.has_unpriced is False

    def test_no_pricing_data_when_all_rows_unpriced(self):
        """A row with no matching PricingRule has `cost=0` and `pricing_rule=None` -
        the trace detail page renders "no pricing data" for this rather than "$0.00"."""
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(team, cost="0", when=_NOW, trace=trace, service_kind=ServiceKind.LLM_INPUT, quantity=100)

        usage = trace_token_usage(trace)

        assert usage.total_cost == Decimal(0)
        assert usage.has_unpriced is True

    def test_flags_estimated_and_unknown_confidence(self):
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(
            team,
            cost="0",
            when=_NOW,
            trace=trace,
            service_kind=ServiceKind.LLM_INPUT,
            quantity=100,
            confidence=Confidence.ESTIMATED,
        )
        _usage(
            team,
            cost="0",
            when=_NOW,
            trace=trace,
            service_kind=ServiceKind.LLM_OUTPUT,
            quantity=None,
            confidence=Confidence.UNKNOWN,
        )

        usage = trace_token_usage(trace)

        assert usage.has_estimated is True
        assert usage.has_unknown is True

    def test_confidence_flags_scoped_per_model(self):
        """An EXACT row for one model must not be flagged just because a
        different model in the same trace has an ESTIMATED row."""
        team = TeamFactory.create()
        trace = TraceFactory.create(team=team)
        _usage(
            team,
            cost="0",
            when=_NOW,
            trace=trace,
            provider_type="openai",
            model_name="gpt-4o",
            service_kind=ServiceKind.LLM_INPUT,
            quantity=100,
            confidence=Confidence.EXACT,
        )
        _usage(
            team,
            cost="0",
            when=_NOW,
            trace=trace,
            provider_type="anthropic",
            model_name="claude-haiku-4-5",
            service_kind=ServiceKind.LLM_INPUT,
            quantity=70,
            confidence=Confidence.ESTIMATED,
        )

        usage = trace_token_usage(trace)

        exact_row = next(m for m in usage.by_model if m.model_name == "gpt-4o")
        estimated_row = next(m for m in usage.by_model if m.model_name == "claude-haiku-4-5")
        assert exact_row.has_estimated is False
        assert estimated_row.has_estimated is True
        assert usage.has_estimated is True


@pytest.mark.django_db()
class TestCoverageGaps:
    """The models behind the unpriced / no-usage warning counts."""

    def test_groups_unpriced_and_unknown_by_model(self):
        team = TeamFactory.create()
        rule = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="priced-model",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.0001",
        )
        # Unpriced (no rule, non-UNKNOWN) across two calls of one model.
        _usage(
            team, cost="0.00", when=_NOW - timedelta(days=1), model_name="unpriced-model", confidence=Confidence.EXACT
        )
        _usage(
            team, cost="0.00", when=_NOW - timedelta(days=2), model_name="unpriced-model", confidence=Confidence.EXACT
        )
        # No-usage (UNKNOWN) call of another model.
        _usage(
            team, cost="0.00", when=_NOW - timedelta(days=3), model_name="unknown-model", confidence=Confidence.UNKNOWN
        )
        # A priced call - must not appear in either list.
        _usage(team, cost="0.50", when=_NOW - timedelta(days=4), model_name="priced-model", pricing_rule=rule)

        gaps = coverage_gaps(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert [(g.model_name, g.call_count) for g in gaps.unpriced] == [("unpriced-model", 2)]
        assert [(g.model_name, g.call_count) for g in gaps.unknown] == [("unknown-model", 1)]

    def test_sorted_by_call_count_descending(self):
        team = TeamFactory.create()
        for _ in range(3):
            _usage(team, cost="0.00", when=_NOW - timedelta(days=1), model_name="loud", confidence=Confidence.EXACT)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), model_name="quiet", confidence=Confidence.EXACT)

        gaps = coverage_gaps(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert [g.model_name for g in gaps.unpriced] == ["loud", "quiet"]

    def test_single_query(self):
        team = TeamFactory.create()
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), confidence=Confidence.EXACT)

        with CaptureQueriesContext(connection) as ctx:
            coverage_gaps(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert len(ctx.captured_queries) == 1

    def test_empty_when_all_priced(self):
        team = TeamFactory.create()
        rule = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="priced-model",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.0001",
        )
        _usage(team, cost="0.50", when=_NOW - timedelta(days=1), model_name="priced-model", pricing_rule=rule)

        gaps = coverage_gaps(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert gaps.unpriced == []
        assert gaps.unknown == []


@pytest.mark.django_db()
class TestCostTimeseries:
    """Per-bucket spend for the panel's daily-spend chart, split by source."""

    def test_buckets_by_day_ordered(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=2))
        _usage(team, cost="0.50", when=_NOW - timedelta(days=2))
        _usage(team, cost="2.00", when=_NOW - timedelta(days=1))

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert [point["chat"] for point in series] == [1.5, 2.0]

    def test_costs_are_floats(self):
        team = TeamFactory.create()
        _usage(team, cost="1.25", when=_NOW - timedelta(days=1))

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert isinstance(series[0]["chat"], float)

    def test_sources_are_separate_series_in_one_bucket(self):
        team = TeamFactory.create()
        when = _NOW - timedelta(days=1)
        _usage(team, cost="1.00", when=when)
        _usage(team, cost="0.25", when=when, source=UsageSource.EVALUATION)

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert len(series) == 1
        assert series[0]["chat"] == 1.0
        assert series[0]["evaluation"] == 0.25

    def test_buckets_zero_fill_missing_sources(self):
        """Stacked series have to line up bucket for bucket, so a bucket with only
        eval spend still reports a chat figure."""
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=2))
        _usage(team, cost="0.25", when=_NOW - timedelta(days=1), source=UsageSource.EVALUATION)

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert series[0] == {"date": series[0]["date"], "chat": 1.0, "evaluation": 0.0}
        assert series[1] == {"date": series[1]["date"], "chat": 0.0, "evaluation": 0.25}

    def test_team_scoped(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1))

        assert cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW) == []


@pytest.mark.django_db()
class TestUsageTimeseries:
    """Per-bucket cost + tokens for the usage API (tz-aware, Decimal cost)."""

    def test_buckets_carry_cost_and_split_tokens(self):
        team = TeamFactory.create()
        day = datetime(2026, 6, 10, 8, tzinfo=UTC)
        _usage(team, cost="0.10", when=day, service_kind=ServiceKind.LLM_INPUT, quantity=100)
        _usage(team, cost="0.05", when=day, service_kind=ServiceKind.LLM_OUTPUT, quantity=40)
        _usage(team, cost="0.20", when=day + timedelta(days=1), service_kind=ServiceKind.LLM_INPUT, quantity=200)

        series = usage_timeseries(
            team,
            start=datetime(2026, 6, 10, tzinfo=UTC),
            end=datetime(2026, 6, 13, tzinfo=UTC),
            granularity="daily",
            tz=ZoneInfo("UTC"),
        )

        # Only non-empty buckets are returned; the usage service zero-fills the rest.
        assert [(row["cost"], row["prompt"], row["completion"], row["total"]) for row in series] == [
            (Decimal("0.15000000"), 100, 40, 140),
            (Decimal("0.20000000"), 200, 0, 200),
        ]
        assert all(row["currency"] == "USD" for row in series)

    def test_bucket_boundary_honours_tz(self):
        """A record at 23:30 UTC on 10 June is 11 June in Auckland (UTC+12), so the tz decides the bucket."""
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=datetime(2026, 6, 10, 23, 30, tzinfo=UTC), quantity=10)

        series = usage_timeseries(
            team,
            start=datetime(2026, 6, 9, tzinfo=UTC),
            end=datetime(2026, 6, 13, tzinfo=UTC),
            granularity="daily",
            tz=ZoneInfo("Pacific/Auckland"),
        )

        # Daily TruncDate returns the local calendar date.
        assert len(series) == 1
        assert series[0]["bucket"] == datetime(2026, 6, 11).date()


@pytest.mark.django_db()
class TestTokenCounts:
    """Token split by service_kind: prompt = input + cached input, completion = output, total = all."""

    def _record(self, team, kind, quantity, when=_NOW - timedelta(days=1)):
        return UsageRecordFactory.create(team=team, service_kind=kind, quantity=quantity, at=when)

    def test_splits_by_service_kind(self):
        team = TeamFactory.create()
        self._record(team, ServiceKind.LLM_INPUT, 100)
        self._record(team, ServiceKind.LLM_CACHED_INPUT, 20)
        self._record(team, ServiceKind.LLM_OUTPUT, 40)
        self._record(team, ServiceKind.LLM_CACHE_WRITE, 5)

        counts = token_counts(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert counts.prompt == 120  # input + cached input
        assert counts.completion == 40  # output
        assert counts.total == 165  # every LLM kind, including cache-write

    def test_zeroes_empty_window(self):
        team = TeamFactory.create()
        self._record(team, ServiceKind.LLM_INPUT, 100, when=_NOW - timedelta(days=40))  # outside window

        counts = token_counts(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert (counts.prompt, counts.completion, counts.total) == (0, 0, 0)

    def test_scoped_to_team(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        self._record(other, ServiceKind.LLM_INPUT, 999)

        counts = token_counts(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert counts.total == 0

    def test_honours_participant_filter(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        keep = ExperimentSessionFactory.create(experiment=exp, team=team)
        drop = ExperimentSessionFactory.create(experiment=exp, team=team)
        UsageRecordFactory.create(
            team=team, service_kind=ServiceKind.LLM_INPUT, quantity=10, participant=keep.participant, at=_NOW
        )
        UsageRecordFactory.create(
            team=team, service_kind=ServiceKind.LLM_INPUT, quantity=99, participant=drop.participant, at=_NOW
        )

        counts = token_counts(
            team,
            start=_NOW - timedelta(days=30),
            end=_NOW + timedelta(days=1),
            filters=CostFilters(participant_ids=[keep.participant_id]),
        )

        assert counts.prompt == 10

    def test_single_window_scoped_query(self):
        team = TeamFactory.create()
        self._record(team, ServiceKind.LLM_INPUT, 100)

        with CaptureQueriesContext(connection) as ctx:
            token_counts(team, start=_NOW - timedelta(days=30), end=_NOW)

        # A single aggregate, window-filtered on the queryset so it index-ranges on
        # (team, timestamp) rather than scanning the team's whole history.
        assert len(ctx.captured_queries) == 1
        assert "timestamp" in ctx.captured_queries[0]["sql"]


@pytest.mark.django_db()
class TestCostTotal:
    def test_sums_period_records_excluding_outside(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))
        _usage(team, cost="0.50", when=_NOW - timedelta(days=2))
        _usage(team, cost="9.99", when=_NOW - timedelta(days=40))  # outside window

        result = cost_total(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert result.total == Decimal("1.50")

    def test_scoped_to_team(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        _usage(other, cost="9.99", when=_NOW - timedelta(days=1))

        result = cost_total(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert result.total == Decimal(0)

    def test_single_query_for_total_and_currency(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))

        with CaptureQueriesContext(connection) as ctx:
            cost_total(team, start=_NOW - timedelta(days=30), end=_NOW)

        # One grouped aggregate covers both total and currency - no prior-period scan, no second query.
        assert len(ctx.captured_queries) == 1

    def test_honours_participant_filter(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        keep = ExperimentSessionFactory.create(experiment=exp, team=team)
        drop = ExperimentSessionFactory.create(experiment=exp, team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=keep.participant)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), participant=drop.participant)

        result = cost_total(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(participant_ids=[keep.participant_id])
        )

        assert result.total == Decimal("1.00")

    @pytest.mark.parametrize(
        ("currencies", "expected"),
        [
            pytest.param(["EUR"], "EUR", id="single-currency-present"),
            pytest.param([], "USD", id="empty-defaults-usd"),
            pytest.param(["USD", "EUR"], "USD", id="mixed-defaults-usd"),
        ],
    )
    def test_currency(self, currencies, expected):
        team = TeamFactory.create()
        for currency in currencies:
            _usage(team, cost="0.10", when=_NOW - timedelta(days=1), currency=currency)

        assert cost_total(team, start=_NOW - timedelta(days=30), end=_NOW).currency == expected

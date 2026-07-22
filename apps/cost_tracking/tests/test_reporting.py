"""Tests for the cost-tracking read path (`services/reporting.py`)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind
from apps.cost_tracking.services.reporting import (
    CostFilters,
    cost_summary,
    cost_timeseries,
    cost_total,
    costs_by_experiment,
    coverage_gaps,
    session_usage,
    token_counts,
    usage_timeseries,
)
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


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
        assert summary.unknown_call_count == 2

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
class TestSessionUsage:
    """Per-session, per-model cost/token breakdown and session scoping."""

    def test_empty_when_no_records(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        usage = session_usage(session)

        assert usage.total_cost == Decimal(0)
        assert usage.by_model == []

    def test_groups_by_model_with_total(self):
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW, session=session, model_name="gpt-4o", quantity=100)
        _usage(team, cost="2.00", when=_NOW, session=session, model_name="gpt-4o", quantity=200)
        _usage(team, cost="0.50", when=_NOW, session=session, model_name="gpt-4o-mini", quantity=50)

        usage = session_usage(session)

        assert usage.total_cost == Decimal("3.50000000")
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
    """Per-bucket spend for the panel's daily-spend chart."""

    def test_buckets_by_day_ordered(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=2))
        _usage(team, cost="0.50", when=_NOW - timedelta(days=2))
        _usage(team, cost="2.00", when=_NOW - timedelta(days=1))

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert [point["cost"] for point in series] == [1.5, 2.0]

    def test_costs_are_floats(self):
        team = TeamFactory.create()
        _usage(team, cost="1.25", when=_NOW - timedelta(days=1))

        series = cost_timeseries(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert isinstance(series[0]["cost"], float)

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


@pytest.mark.django_db()
class TestCostFilters:
    """The cost read path honours the dashboard's chatbot / participant /
    platform filters (but not tags). Verified across the four public functions.
    """

    def test_cost_summary_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=drop)

        summary = cost_summary(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert summary.total_cost == Decimal("1.00")

    def test_cost_summary_filters_prior_period_too(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=45), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=45), experiment=drop)

        summary = cost_summary(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert summary.previous_period_cost == Decimal("2.00")

    def test_timeseries_filters_by_participant(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        keep = ExperimentSessionFactory.create(experiment=exp, team=team)
        drop = ExperimentSessionFactory.create(experiment=exp, team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, participant=keep.participant)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, participant=drop.participant)

        series = cost_timeseries(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(participant_ids=[keep.participant_id])
        )

        assert [point["cost"] for point in series] == [1.0]

    def test_timeseries_filters_by_platform_via_session(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        web = ExperimentSessionFactory.create(experiment=exp, team=team, platform="web")
        api = ExperimentSessionFactory.create(experiment=exp, team=team, platform="api")
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=web)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=exp, session=api)

        series = cost_timeseries(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(platform_names=["web"])
        )

        assert [point["cost"] for point in series] == [1.0]

    def test_costs_by_experiment_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=keep)
        _usage(team, cost="9.00", when=_NOW - timedelta(days=1), experiment=drop)

        costs = costs_by_experiment(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert costs == {keep.id: Decimal("1.00000000")}

    def test_coverage_gaps_filters_by_experiment(self):
        team = TeamFactory.create()
        keep = ExperimentFactory.create(team=team)
        drop = ExperimentFactory.create(team=team)
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), experiment=keep, model_name="keep-model")
        _usage(team, cost="0.00", when=_NOW - timedelta(days=1), experiment=drop, model_name="drop-model")

        gaps = coverage_gaps(
            team, start=_NOW - timedelta(days=30), end=_NOW, filters=CostFilters(experiment_ids=[keep.id])
        )

        assert [g.model_name for g in gaps.unpriced] == ["keep-model"]

"""Tests for the cost-tracking read path (`services/reporting.py`)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageRecord
from apps.cost_tracking.services.reporting import (
    cost_summary,
    last_synced_at,
    top_n_bots,
)
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _usage(
    team,
    *,
    cost,
    when,
    confidence=Confidence.EXACT,
    experiment=None,
    session=None,
    quantity=100,
):
    """Create a UsageRecord at a specific time. Direct ORM update is needed
    because `timestamp` uses `auto_now_add` which can't be set via .create().
    """
    record = UsageRecord.objects.create(
        team=team,
        service_kind=ServiceKind.LLM_INPUT,
        provider_type="openai",
        model_name="gpt-4o-mini",
        quantity=quantity,
        unit_price=Decimal("0.00015"),
        cost=Decimal(str(cost)),
        confidence=confidence,
        experiment=experiment,
        session=session,
    )
    UsageRecord.objects.filter(pk=record.pk).update(timestamp=when)
    record.refresh_from_db()
    return record


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

    def test_last_synced_returned_in_summary(self):
        team = TeamFactory.create()
        newer = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="test-model-newer",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00015",
        )
        future = newer.effective_from + timedelta(days=365)
        PricingRule.objects.filter(pk=newer.pk).update(effective_from=future)

        summary = cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert summary.last_synced == future

    def test_single_query_for_aggregate(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1))

        with CaptureQueriesContext(connection) as ctx:
            cost_summary(team, start=_NOW - timedelta(days=30), end=_NOW)

        # One aggregate over UsageRecord + one for last_synced — no N+1.
        assert len(ctx.captured_queries) == 2


@pytest.mark.django_db()
class TestTopNBots:
    """Ordering, exclusions, aggregation, team scoping."""

    def test_orders_by_cost_descending(self):
        team = TeamFactory.create()
        cheap = ExperimentFactory.create(team=team, name="cheap")
        expensive = ExperimentFactory.create(team=team, name="expensive")
        _usage(team, cost="0.10", when=_NOW - timedelta(days=1), experiment=cheap)
        _usage(team, cost="5.00", when=_NOW - timedelta(days=1), experiment=expensive)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert [r.experiment_name for r in rows] == ["expensive", "cheap"]

    def test_excludes_records_with_null_experiment(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=None)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert rows == []

    def test_aggregates_per_experiment(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team, name="bot")
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, quantity=100)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=2), experiment=exp, quantity=200)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert len(rows) == 1
        assert rows[0].cost == Decimal("3.00000000")
        assert rows[0].tokens == 300

    def test_cost_per_session_divides_when_sessions_present(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        s1 = ExperimentSessionFactory.create(experiment=exp, team=team)
        s2 = ExperimentSessionFactory.create(experiment=exp, team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=s1)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=s2)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert rows[0].sessions == 2
        assert rows[0].cost_per_session == Decimal("1.00000000")

    def test_cost_per_session_none_when_no_sessions(self):
        team = TeamFactory.create()
        exp = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=exp, session=None)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert rows[0].sessions == 0
        assert rows[0].cost_per_session is None

    def test_limit_truncates(self):
        team = TeamFactory.create()
        for i in range(15):
            exp = ExperimentFactory.create(team=team, name=f"bot-{i}")
            _usage(team, cost=str(i + 1), when=_NOW - timedelta(days=1), experiment=exp)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW, limit=5)

        assert len(rows) == 5

    def test_team_scoped(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        exp_other = ExperimentFactory.create(team=other)
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1), experiment=exp_other)

        rows = top_n_bots(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert rows == []


@pytest.mark.django_db()
class TestLastSyncedAt:
    """The seed migration always inserts global rules — these tests prune
    them first so the assertions speak only to behaviour under test.
    """

    def test_none_when_no_global_rules(self):
        PricingRule.objects.all().delete()
        assert last_synced_at() is None

    def test_ignores_team_scoped_rules(self):
        PricingRule.objects.filter(team__isnull=True).delete()
        team = TeamFactory.create()
        PricingRule.objects.create(
            team=team,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00015",
        )

        assert last_synced_at() is None

    def test_returns_most_recent_global_effective_from(self):
        newer = PricingRule.objects.create(
            team=None,
            provider_type="openai",
            model_name="test-model-newer",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00015",
        )
        future = newer.effective_from + timedelta(days=365)
        PricingRule.objects.filter(pk=newer.pk).update(effective_from=future)

        assert last_synced_at() == future

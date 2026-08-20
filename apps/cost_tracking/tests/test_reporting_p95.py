"""Tests for the p95 cost-per-trace read path (`services/reporting.py`).
Per-function aggregation behaviour for the other reporting helpers lives in
`test_reporting.py`.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.reporting import p95_cost_per_trace
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory
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
class TestP95CostPerTrace:
    """Per-chatbot p95 of per-trace cost, bucketed for the dashboard line chart."""

    def test_p95_over_per_trace_totals(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        day = _NOW - timedelta(days=1)
        # One trace split across two records - must sum to a single per-trace total.
        split = TraceFactory.create(team=team)
        _usage(team, cost="0.60", when=day, experiment=experiment, trace=split)
        _usage(team, cost="0.40", when=day, experiment=experiment, trace=split)
        for cost in ("2.00", "3.00", "5.00"):
            _usage(team, cost=cost, when=day, experiment=experiment, trace=TraceFactory.create(team=team))

        series = p95_cost_per_trace(team, start=_START, end=_NOW)

        # Per-trace totals are [1.0, 2.0, 3.0, 5.0]; nearest-rank p95 of four
        # values is the top one.
        assert series == [
            {
                "experiment_id": experiment.id,
                "experiment_name": experiment.name,
                "points": [{"date": day.date(), "p95": 5.0}],
            }
        ]

    def test_buckets_are_chronological(self):
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        newer = _NOW - timedelta(days=1)
        older = _NOW - timedelta(days=3)
        _usage(team, cost="2.00", when=newer, experiment=experiment, trace=TraceFactory.create(team=team))
        _usage(team, cost="1.00", when=older, experiment=experiment, trace=TraceFactory.create(team=team))

        series = p95_cost_per_trace(team, start=_START, end=_NOW)

        assert series[0]["points"] == [
            {"date": older.date(), "p95": 1.0},
            {"date": newer.date(), "p95": 2.0},
        ]

    def test_caps_to_top_n_chatbots_by_spend(self):
        team = TeamFactory.create()
        experiments = ExperimentFactory.create_batch(3, team=team)
        for cost, experiment in zip(("1.00", "3.00", "2.00"), experiments, strict=True):
            _usage(
                team,
                cost=cost,
                when=_NOW - timedelta(days=1),
                experiment=experiment,
                trace=TraceFactory.create(team=team),
            )

        series = p95_cost_per_trace(team, start=_START, end=_NOW, top_n=2)

        assert [line["experiment_id"] for line in series] == [experiments[1].id, experiments[2].id]

    def test_excludes_untraced_and_evaluation_records(self):
        """Records without a trace have no per-trace cost; evaluation spend is
        never a chatbot's (ADR-0048), and eval traces are evaluation spend
        (ADR-0050)."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), experiment=experiment)
        _usage(
            team,
            cost="9.00",
            when=_NOW - timedelta(days=1),
            experiment=experiment,
            trace=TraceFactory.create(team=team),
            source=UsageSource.EVALUATION,
        )

        series = p95_cost_per_trace(team, start=_START, end=_NOW)

        assert series == []

    def test_two_queries(self):
        """One grouped scan plus one name lookup - no per-experiment queries."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team)
        _usage(
            team,
            cost="1.00",
            when=_NOW - timedelta(days=1),
            experiment=experiment,
            trace=TraceFactory.create(team=team),
        )

        with CaptureQueriesContext(connection) as ctx:
            p95_cost_per_trace(team, start=_START, end=_NOW)

        assert len(ctx.captured_queries) == 2

    def test_resolves_name_for_archived_chatbot(self):
        """The name lookup must not go through the plain (filtered) manager - a chatbot
        archived after spending in the window would otherwise resolve to no name at all,
        leaving its line in the chart with a blank legend entry."""
        team = TeamFactory.create()
        experiment = ExperimentFactory.create(team=team, name="Archived Bot")
        _usage(
            team,
            cost="1.00",
            when=_NOW - timedelta(days=1),
            experiment=experiment,
            trace=TraceFactory.create(team=team),
        )
        experiment.archive()

        series = p95_cost_per_trace(team, start=_START, end=_NOW)

        assert series[0]["experiment_name"] == "Archived Bot"

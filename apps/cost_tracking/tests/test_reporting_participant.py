"""Tests for the per-participant cost read path (`services/reporting.py`).
Per-function aggregation behaviour for the other reporting helpers lives in
`test_reporting.py`.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.reporting import CostFilters, costs_by_participant
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.team import TeamFactory

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
# The 30-day window most tests read over, named where a test needs it inside a lambda.
_START = _NOW - timedelta(days=30)


def _usage(team, *, cost, when, **kwargs):
    """Thin wrapper around UsageRecordFactory that coerces `cost` to Decimal
    and forwards optional kwargs (confidence, experiment, session, quantity).
    """
    return UsageRecordFactory.create(team=team, cost=Decimal(str(cost)), at=when, **kwargs)


@pytest.mark.django_db()
class TestCostsByParticipant:
    """Per-participant cost map feeding the dashboard's Most Active Participants
    table and the participants page cost column."""

    def test_single_query(self):
        team = TeamFactory.create()
        first = ParticipantFactory.create(team=team)
        second = ParticipantFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=first)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=2), participant=second)

        with CaptureQueriesContext(connection) as ctx:
            costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW)

        # Single GROUP BY query - no N+1 per participant.
        assert len(ctx.captured_queries) == 1

    def test_aggregates_per_participant(self):
        team = TeamFactory.create()
        participant = ParticipantFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=participant)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=2), participant=participant)

        costs = costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert costs == {participant.id: Decimal("3.00000000")}

    def test_excludes_records_with_null_participant(self):
        team = TeamFactory.create()
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=None)

        assert costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW) == {}

    def test_excludes_evaluation_spend(self):
        team = TeamFactory.create()
        participant = ParticipantFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=participant)
        _usage(
            team,
            cost="9.00",
            when=_NOW - timedelta(days=1),
            participant=participant,
            source=UsageSource.EVALUATION,
        )

        costs = costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW)

        assert costs == {participant.id: Decimal("1.00000000")}

    def test_excludes_records_outside_window(self):
        team = TeamFactory.create()
        participant = ParticipantFactory.create(team=team)
        _usage(team, cost="9.99", when=_NOW - timedelta(days=40), participant=participant)

        assert costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW) == {}

    def test_team_scoped(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        participant_other = ParticipantFactory.create(team=other)
        _usage(other, cost="999.00", when=_NOW - timedelta(days=1), participant=participant_other)

        assert costs_by_participant(team, start=_NOW - timedelta(days=30), end=_NOW) == {}

    def test_bounded_by_participant_filter(self):
        """Callers bound the read to a page / top-N of participants via CostFilters."""
        team = TeamFactory.create()
        wanted = ParticipantFactory.create(team=team)
        unwanted = ParticipantFactory.create(team=team)
        _usage(team, cost="1.00", when=_NOW - timedelta(days=1), participant=wanted)
        _usage(team, cost="2.00", when=_NOW - timedelta(days=1), participant=unwanted)

        costs = costs_by_participant(
            team,
            start=_NOW - timedelta(days=30),
            end=_NOW,
            filters=CostFilters(participant_ids=[wanted.id]),
        )

        assert costs == {wanted.id: Decimal("1.00000000")}

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.experiments.models import ParticipantData
from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


def _make_trace(session, participant_data, timestamp_offset_minutes=0, **kwargs):
    """Helper to create a trace with specific participant_data and timestamp."""
    trace = TraceFactory.create(
        session=session,
        experiment=session.experiment,
        team=session.team,
        participant=session.participant,
        participant_data=participant_data,
        **kwargs,
    )
    if timestamp_offset_minutes:
        # Update timestamp directly since auto_now_add prevents setting it in create
        Trace.objects.filter(id=trace.id).update(timestamp=timezone.now() + timedelta(minutes=timestamp_offset_minutes))
        trace.refresh_from_db()
    return trace


@pytest.fixture()
def team():
    return TeamFactory.create()


@pytest.fixture()
def experiment(team):
    return ExperimentFactory.create(team=team)


@pytest.fixture()
def participant(team):
    return ParticipantFactory.create(team=team)


@pytest.mark.django_db()
class TestBackfillParticipantDataDiff:
    def _call_command(self, team_slug, since_date="2020-01-01", **kwargs):
        call_command(
            "backfill_participant_data_diff",
            team_slug,
            since_date,
            **kwargs,
        )

    def test_same_session_consecutive_traces_with_diff(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)
        trace2 = _make_trace(session, {"name": "Alice", "age": 30}, timestamp_offset_minutes=1)
        # trace3 exists so trace2 has a "next" trace
        _make_trace(session, {"name": "Alice", "age": 30}, timestamp_offset_minutes=2)

        self._call_command(team.slug)

        trace1.refresh_from_db()
        trace2.refresh_from_db()

        # trace1's diff: {"name": "Alice"} -> {"name": "Alice", "age": 30}
        assert trace1.participant_data_diff == [["add", "", [["age", 30]]]]

        # trace2's diff should be empty since trace2 and trace3 have same data
        assert trace2.participant_data_diff == []

    def test_same_session_no_diff(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)
        _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=1)

        self._call_command(team.slug)

        trace1.refresh_from_db()
        # No diff because participant_data is the same
        assert trace1.participant_data_diff == []

    def test_last_trace_in_session_uses_next_session(self, team, experiment, participant):
        session1 = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)
        session2 = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        # Last trace in session1
        trace1 = _make_trace(session1, {"name": "Alice"}, timestamp_offset_minutes=0)
        # First trace in session2 shows the data changed
        _make_trace(session2, {"name": "Alice", "score": 10}, timestamp_offset_minutes=5)

        self._call_command(team.slug)

        trace1.refresh_from_db()
        # {"name": "Alice"} -> {"name": "Alice", "score": 10}
        assert trace1.participant_data_diff == [["add", "", [["score", 10]]]]

    def test_last_trace_falls_back_to_global_participant_data(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)

        # No next session, but global ParticipantData exists with different data
        ParticipantData.objects.create(
            team=team,
            participant=participant,
            experiment=experiment,
            data={"name": "Alice", "updated": True},
        )

        self._call_command(team.slug)

        trace1.refresh_from_db()
        # {"name": "Alice"} -> {"name": "Alice", "updated": True}
        assert trace1.participant_data_diff == [["add", "", [["updated", True]]]]

    def test_no_diff_when_global_data_matches(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)

        ParticipantData.objects.create(
            team=team,
            participant=participant,
            experiment=experiment,
            data={"name": "Alice"},
        )

        self._call_command(team.slug)

        trace1.refresh_from_db()
        assert trace1.participant_data_diff == []

    def test_experiment_filter(self, team, participant):
        exp1 = ExperimentFactory.create(team=team)
        exp2 = ExperimentFactory.create(team=team)

        session1 = ExperimentSessionFactory.create(experiment=exp1, team=team, participant=participant)
        session2 = ExperimentSessionFactory.create(experiment=exp2, team=team, participant=participant)

        trace1 = _make_trace(session1, {"name": "Alice"}, timestamp_offset_minutes=0)
        _make_trace(session1, {"name": "Bob"}, timestamp_offset_minutes=1)

        trace2 = _make_trace(session2, {"x": 1}, timestamp_offset_minutes=0)
        _make_trace(session2, {"x": 2}, timestamp_offset_minutes=1)

        # Only backfill exp1
        self._call_command(team.slug, experiment_id=exp1.id)

        trace1.refresh_from_db()
        trace2.refresh_from_db()

        # {"name": "Alice"} -> {"name": "Bob"}
        assert trace1.participant_data_diff == [["change", "name", ["Alice", "Bob"]]]
        # trace2 should not be updated since we filtered by exp1
        assert trace2.participant_data_diff == []

    def test_since_date_filter(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)
        _make_trace(session, {"name": "Bob"}, timestamp_offset_minutes=1)

        # Use a future date so no traces match
        future_date = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self._call_command(team.slug, since_date=future_date)

        trace1.refresh_from_db()
        assert trace1.participant_data_diff == []

    def test_dry_run_does_not_modify(self, team, experiment, participant):
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)
        _make_trace(session, {"name": "Bob"}, timestamp_offset_minutes=1)

        call_command(
            "backfill_participant_data_diff",
            team.slug,
            "2020-01-01",
            dry_run=True,
        )

        trace1.refresh_from_db()
        assert trace1.participant_data_diff == []

    def test_invalid_team_slug(self, capsys):
        call_command("backfill_participant_data_diff", "nonexistent-team", "2020-01-01")
        output = capsys.readouterr().err
        assert "not found" in output

    def test_does_not_overwrite_existing_diff(self, team, experiment, participant):
        """Traces with an existing participant_data_diff should not be overwritten."""
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        existing_diff = [["change", "name", ["Original", "Diff"]]]
        trace1 = _make_trace(
            session, {"name": "Alice"}, timestamp_offset_minutes=0, participant_data_diff=existing_diff
        )
        _make_trace(session, {"name": "Bob"}, timestamp_offset_minutes=1)

        self._call_command(team.slug)

        trace1.refresh_from_db()
        # The existing diff should be preserved, not overwritten with the backfilled one
        assert trace1.participant_data_diff == existing_diff

    def test_dictdiffer_format(self, team, experiment, participant):
        """Verify the diff format matches what dictdiffer produces."""
        session = ExperimentSessionFactory.create(experiment=experiment, team=team, participant=participant)

        trace1 = _make_trace(session, {"name": "Alice"}, timestamp_offset_minutes=0)
        _make_trace(session, {"name": "Bob", "age": 25}, timestamp_offset_minutes=1)
        # Need a third trace so trace2 has a next
        _make_trace(session, {"name": "Bob", "age": 25}, timestamp_offset_minutes=2)

        self._call_command(team.slug)

        trace1.refresh_from_db()
        # {"name": "Alice"} -> {"name": "Bob", "age": 25}
        assert trace1.participant_data_diff == [
            ["change", "name", ["Alice", "Bob"]],
            ["add", "", [["age", 25]]],
        ]

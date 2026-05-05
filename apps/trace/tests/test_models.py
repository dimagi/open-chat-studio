import pytest
from django.db import connection

from apps.trace.models import Trace
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_trace_expected_indexes_exist():
    """Smoke check that the indexes the list-view hot path depends on are present."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'trace_trace'")
        names = {row[0] for row in cursor.fetchall()}

    assert "trace_team_timestamp_idx" in names
    assert "trace_experiment_timestamp_idx" in names
    assert "trace_session_timestamp_idx" in names


@pytest.mark.django_db()
def test_trace_participant_data_diff_defaults_to_empty_list():
    """The new participant_data_diff field should default to an empty list."""
    experiment = ExperimentFactory()
    session = ExperimentSessionFactory(experiment=experiment)
    trace = Trace.objects.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=experiment.team,
        duration=100,
        participant_data={"name": "Alice"},
    )
    trace.refresh_from_db()
    assert trace.participant_data_diff == []


@pytest.mark.django_db()
def test_trace_participant_data_diff_stores_and_retrieves_diff():
    """The field should round-trip a dictdiffer-style diff list through the DB."""
    experiment = ExperimentFactory()
    session = ExperimentSessionFactory(experiment=experiment)
    diff = [["change", "plan", ["free", "pro"]], ["add", "", [["score", 100]]]]
    trace = Trace.objects.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=experiment.team,
        duration=100,
        participant_data={"name": "Alice", "plan": "pro", "score": 100},
        participant_data_diff=diff,
    )
    trace.refresh_from_db()
    assert trace.participant_data_diff == diff

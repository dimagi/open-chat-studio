import pytest
from django.db import connection
from django.db.models import ProtectedError
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus, Evaluator, InFlightRunsError
from apps.teams.models import Team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationResultFactory,
    EvaluationRunAggregateFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_archive_sets_the_flag_and_leaves_config_membership_intact(team_with_users):
    """Detaching was rejected: apps/assessments/views.py:41 reads live membership."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])

    evaluator.archive()

    evaluator.refresh_from_db()
    assert evaluator.is_archived is True
    assert list(config.evaluators.all()) == [evaluator]


@pytest.mark.django_db()
def test_unarchive_clears_the_flag(team_with_users):
    """Unarchiving a previously archived evaluator sets is_archived back to False."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    evaluator.archive()

    evaluator.unarchive()

    evaluator.refresh_from_db()
    assert evaluator.is_archived is False


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(EvaluationRunStatus.PENDING, id="pending"),
        pytest.param(EvaluationRunStatus.PROCESSING, id="processing"),
    ],
)
def test_archive_blocked_while_a_run_is_in_flight(status, team_with_users):
    """Archiving raises InFlightRunsError and leaves is_archived False while a run is pending or processing."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    EvaluationRunFactory.create(team=team_with_users, config=config, status=status, evaluator_ids=[evaluator.id])
    config.evaluators.clear()

    with pytest.raises(InFlightRunsError):
        evaluator.archive()

    evaluator.refresh_from_db()
    assert evaluator.is_archived is False


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "history",
    [
        pytest.param("result", id="results-only"),
        pytest.param("aggregate", id="aggregates-only"),
    ],
)
def test_deleting_an_evaluator_with_history_is_refused_by_the_database(history, team_with_users):
    """Both FKs must be PROTECT. Disjoint setups so one flipped FK cannot pass both."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
    if history == "result":
        EvaluationResultFactory.create(team=team_with_users, run=run, evaluator=evaluator)
    else:
        EvaluationRunAggregateFactory.create(run=run, evaluator=evaluator)

    with pytest.raises(ProtectedError):
        evaluator.delete()

    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_forward_access_still_resolves_an_archived_evaluator(team_with_users):
    """A result's evaluator FK still resolves to the evaluator after it is archived."""
    evaluator = EvaluatorFactory.create(team=team_with_users, name="Accuracy")
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
    result = EvaluationResultFactory.create(team=team_with_users, run=run, evaluator=evaluator)

    evaluator.archive()

    assert type(result).objects.get(id=result.id).evaluator.name == "Accuracy"


@pytest.mark.django_db()
def test_is_archived_keeps_a_database_level_default():
    """A rolling deploy inserts Evaluator rows from the previous release, which omit this
    column. Django drops a plain `default` after AddField; `db_default` survives."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            [Evaluator._meta.db_table, "is_archived"],
        )
        (column_default,) = cursor.fetchone()

    assert column_default == "false"


@pytest.mark.django_db()
def test_csv_export_still_contains_an_archived_evaluators_column(client, team_with_users):
    """#3861's acceptance criterion: archiving preserves a past run's exported history."""
    evaluator = EvaluatorFactory.create(team=team_with_users, name="Retired Judge")
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    run = EvaluationRunFactory.create(
        team=team_with_users,
        config=config,
        status=EvaluationRunStatus.COMPLETED,
        evaluator_ids=[evaluator.id],
    )
    EvaluationResultFactory.create(
        team=team_with_users,
        run=run,
        evaluator=evaluator,
        output={"result": {"score": 8.5}},
    )
    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluation_run_download", args=[team_with_users.slug, config.id, run.id])
    before = client.get(url).content.decode()

    evaluator.archive()
    after = client.get(url).content.decode()

    assert "score (Retired Judge)" in after
    assert after == before


@pytest.mark.django_db()
def test_the_default_manager_never_hides_archived_evaluators(team_with_users):
    """Historical read paths (results table, cost reporting, tag-deletion chips, concordance) resolve via `objects`."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    evaluator.archive()

    assert Evaluator.objects.filter(id=evaluator.id).exists()
    assert Evaluator.objects.filter(team=team_with_users).count() == 1


@pytest.mark.django_db()
def test_deleting_a_team_with_an_archived_evaluator_and_history_succeeds():
    """Pins the ordering interaction between PROTECT and _deletion_order."""
    team = TeamFactory.create()
    evaluator = EvaluatorFactory.create(team=team)
    config = EvaluationConfigFactory.create(team=team, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team, config=config, status=EvaluationRunStatus.COMPLETED)
    EvaluationResultFactory.create(team=team, run=run, evaluator=evaluator)
    EvaluationRunAggregateFactory.create(run=run, evaluator=evaluator)
    evaluator.archive()

    delete_object_with_auditing_of_related_objects(team)

    assert not Team.objects.filter(id=team.id).exists()
    assert not Evaluator.objects.filter(id=evaluator.id).exists()

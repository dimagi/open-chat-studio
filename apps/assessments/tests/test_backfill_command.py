import pytest
from django.core.management import call_command

from apps.assessments.models import Score
from apps.human_annotations.models import Annotation, AnnotationStatus
from apps.utils.factories.evaluations import (
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def historical_data(db):
    """Build EvaluationResults and Annotations directly via the ORM, bypassing the
    dual-write hooks, so we can prove the backfill produces the same rows."""
    team = TeamWithUsersFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    evaluator = EvaluatorFactory.create(team=team)
    message = EvaluationMessageFactory.create(session=session)
    run = EvaluationRunFactory.create(team=team)
    result = EvaluationResultFactory.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={"result": {"sentiment": "positive", "score": 5}},
    )
    # EvaluationResultFactory doesn't go through the Celery task, so no scores
    # should exist yet. Wipe any accidental rows just to be sure.
    Score.objects.filter(automated_result=result).delete()

    queue = AnnotationQueueFactory.create(
        team=team,
        schema={"sentiment": {"type": "choice", "choices": ["positive", "negative"], "description": "x"}},
    )
    item = AnnotationItemFactory.create(queue=queue, session=session, team=team)
    # Build the Annotation via bulk_create to skip Annotation.save's dual-write hook.
    Annotation.objects.bulk_create(
        [
            Annotation(
                team=team,
                item=item,
                reviewer=team.members.first(),
                data={"sentiment": "positive"},
                status=AnnotationStatus.SUBMITTED,
            ),
        ]
    )
    # Verify pre-state: no Scores
    assert Score.objects.count() == 0
    return team, session, result


@pytest.mark.django_db()
def test_backfill_dry_run_reports_counts(historical_data, capsys):
    call_command("backfill_initial_scores", "--dry-run")
    captured = capsys.readouterr()
    assert "Would write Scores" in captured.out
    assert Score.objects.count() == 0  # dry-run wrote nothing


@pytest.mark.django_db()
def test_backfill_writes_scores_for_historical_data(historical_data):
    team, session, result = historical_data
    assert Score.objects.count() == 0

    call_command("backfill_initial_scores")

    auto_scores = Score.objects.filter(automated_result=result)
    assert {s.name for s in auto_scores} == {"sentiment", "score"}

    review_scores = Score.objects.filter(review__isnull=False)
    assert review_scores.count() == 1
    assert review_scores.first().name == "sentiment"


@pytest.mark.django_db()
def test_backfill_is_idempotent_on_force_rerun(historical_data):
    call_command("backfill_initial_scores")
    first_total = Score.objects.count()
    call_command("backfill_initial_scores", "--force")
    second_total = Score.objects.count()
    assert first_total == second_total

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.assessments.models import Score
from apps.assessments.score_writers import write_scores_from_evaluation_result
from apps.evaluations.models import EvaluationResult
from apps.human_annotations.models import Annotation, AnnotationStatus
from apps.utils.factories.evaluations import (
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_dual_write_produces_concordance_ready_scores():
    """Smoke test: an EvaluationResult and an Annotation on the same session
    both produce Score rows targeting the same ExperimentSession, queryable
    via the same GFK keys."""
    team = TeamWithUsersFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    schema = {"verdict": {"type": "choice", "choices": ["yes", "no"], "description": "x"}}

    # Eval side
    evaluator = EvaluatorFactory.create(team=team, params={"llm_prompt": "x", "output_schema": schema})
    run = EvaluationRunFactory.create(team=team)
    message = EvaluationMessageFactory.create(session=session)
    result = EvaluationResult.objects.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={"result": {"verdict": "yes"}},
    )
    # Direct factory call (not via Celery), so manually invoke the writer.
    write_scores_from_evaluation_result(result)

    # Human side
    queue = AnnotationQueueFactory.create(team=team, schema=schema)
    item = AnnotationItemFactory.create(queue=queue, session=session, team=team)
    Annotation.objects.create(
        team=team,
        item=item,
        reviewer=team.members.first(),
        data={"verdict": "no"},
        status=AnnotationStatus.SUBMITTED,
    )

    session_ct = ContentType.objects.get_for_model(session)
    scores_on_session = Score.objects.filter(
        team=team,
        target_content_type=session_ct,
        target_object_id=session.id,
        name="verdict",
    )
    sources = {s.source for s in scores_on_session}
    assert Score.Source.LLM_JUDGE in sources
    assert Score.Source.HUMAN_REVIEW in sources

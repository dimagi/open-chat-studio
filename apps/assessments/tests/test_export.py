import csv
import io

import pytest
from django.urls import reverse
from waffle.models import Flag

from apps.assessments.score_writers import write_scores_from_evaluation_result
from apps.evaluations.models import EvaluationResult
from apps.human_annotations.models import Annotation, AnnotationStatus
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


def _enable_flag(name):
    flag, _ = Flag.objects.get_or_create(name=name)
    flag.everyone = True
    flag.save()
    flag.flush()
    return flag


@pytest.fixture()
def concordance_flags():
    """Enable all three feature flags required by the concordance views."""
    _enable_flag("flag_assessments_concordance")
    _enable_flag("flag_evaluations")
    _enable_flag("flag_human_annotations")


def _make_concordance_data(team):
    """Create minimal concordance-ready fixtures; return (eval_config, queue, session)."""
    schema = {"verdict": {"type": "choice", "choices": ["yes", "no"], "description": "x"}}
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)

    evaluator = EvaluatorFactory.create(team=team, params={"llm_prompt": "x", "output_schema": schema})
    eval_config = EvaluationConfigFactory.create(team=team)
    eval_config.evaluators.add(evaluator)
    run = EvaluationRunFactory.create(team=team, config=eval_config)
    message = EvaluationMessageFactory.create(session=session)
    result = EvaluationResult.objects.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={"result": {"verdict": "yes"}},
    )
    write_scores_from_evaluation_result(result)

    queue = AnnotationQueueFactory.create(team=team, schema=schema)
    item = AnnotationItemFactory.create(queue=queue, session=session, team=team)
    Annotation.objects.create(
        team=team,
        item=item,
        reviewer=team.members.first(),
        data={"verdict": "yes"},
        status=AnnotationStatus.SUBMITTED,
    )

    return eval_config, queue, session


@pytest.mark.django_db()
def test_export_concordance_csv(client, concordance_flags):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client.force_login(user)

    eval_config, queue, session = _make_concordance_data(team)

    url = reverse("assessments:concordance_export", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id, "field": "verdict", "show": "all"})

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    reader = csv.DictReader(io.StringIO(response.content.decode()))
    rows = list(reader)
    assert len(rows) >= 1
    first = rows[0]
    assert "kind" in first
    assert "judge_verdict" in first
    assert "human_verdict" in first


@pytest.mark.django_db()
def test_export_concordance_csv_missing_params(client, concordance_flags):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client.force_login(user)

    url = reverse("assessments:concordance_export", args=[team.slug])
    response = client.get(url)
    assert response.status_code == 404

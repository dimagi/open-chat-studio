import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.assessments.score_writers import write_scores_from_evaluation_result
from apps.human_annotations.models import Annotation, AnnotationStatus
from apps.teams.models import Flag
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def concordance_flag(db):
    """Enable the team-managed flag for all callers by default."""
    flag, _ = Flag.objects.get_or_create(name="flag_assessments_concordance")
    flag.everyone = True
    flag.save()
    flag.flush()
    return flag


@pytest.fixture()
def authed_client(client, db):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    # The view requires evaluations + human_annotations view perms — grant both.
    perms = Permission.objects.filter(
        codename__in=[
            "view_evaluationconfig",
            "view_annotationqueue",
        ]
    )
    user.user_permissions.add(*perms)
    client.force_login(user)
    return client, team, user


def _build_concordance_fixtures(team):
    """Eval config + annotation queue sharing a binary `verdict` choice field
    over three sessions. Wiring goes via dual-write hooks (no direct Score writes)."""
    sessions = [ExperimentSessionFactory.create(team=team, experiment__team=team) for _ in range(3)]
    schema = {"verdict": {"type": "choice", "choices": ["yes", "no"], "description": "binary"}}

    evaluator = EvaluatorFactory.create(
        team=team,
        params={"llm_prompt": "x", "output_schema": schema},
    )
    eval_config = EvaluationConfigFactory.create(team=team, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team, config=eval_config)
    queue = AnnotationQueueFactory.create(team=team, schema=schema)

    # Three sessions with various concordance shapes:
    # - sessions[0]: both eval and human, agree
    # - sessions[1]: both eval and human, disagree
    # - sessions[2]: eval only
    judge_values = ["yes", "yes", "no"]
    human_values = ["yes", "no", None]

    for session, judge_value, human_value in zip(sessions, judge_values, human_values, strict=True):
        message = EvaluationMessageFactory.create(session=session)
        result = EvaluationResultFactory.create(
            team=team,
            evaluator=evaluator,
            message=message,
            run=run,
            output={"result": {"verdict": judge_value}},
        )
        # EvaluationResultFactory doesn't go through the Celery task, so write Scores manually
        write_scores_from_evaluation_result(result)

        if human_value is not None:
            item = AnnotationItemFactory.create(queue=queue, session=session, team=team)
            Annotation.objects.create(
                team=team,
                item=item,
                reviewer=team.members.first(),
                data={"verdict": human_value},
                status=AnnotationStatus.SUBMITTED,
            )

    # Add a fourth session that's human-only (no eval) to test the human-only bucket
    human_only_session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    item = AnnotationItemFactory.create(queue=queue, session=human_only_session, team=team)
    Annotation.objects.create(
        team=team,
        item=item,
        reviewer=team.members.first(),
        data={"verdict": "yes"},
        status=AnnotationStatus.SUBMITTED,
    )

    return eval_config, queue


@pytest.mark.django_db()
def test_concordance_picker_form_renders_with_no_params(authed_client, concordance_flag):
    client, team, _ = authed_client
    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url)
    assert response.status_code == 200
    assert b"Concordance" in response.content


@pytest.mark.django_db()
def test_concordance_view_renders_matched_eval_only_human_only_buckets(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id})
    assert response.status_code == 200
    body = response.content.decode()
    # Matched count is 2 (sessions[0] and sessions[1]); agreement count is 1.
    assert "Matched: 2" in body
    assert "Agreement: 1 / 2" in body
    assert "Eval only: 1" in body
    assert "Human only: 1" in body


@pytest.mark.django_db()
def test_concordance_view_filters_to_authoritative_human_score(authed_client, concordance_flag):
    """Multi-reviewer queue: only the authoritative annotation drives the human side."""
    client, team, _ = authed_client
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    schema = {"verdict": {"type": "choice", "choices": ["yes", "no"], "description": "x"}}
    evaluator = EvaluatorFactory.create(team=team, params={"llm_prompt": "x", "output_schema": schema})
    eval_config = EvaluationConfigFactory.create(team=team, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team, config=eval_config)
    message = EvaluationMessageFactory.create(session=session)
    result = EvaluationResultFactory.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={"result": {"verdict": "yes"}},
    )
    write_scores_from_evaluation_result(result)

    queue = AnnotationQueueFactory.create(team=team, schema=schema, num_reviews_required=2)
    item = AnnotationItemFactory.create(queue=queue, session=session, team=team)
    members = list(team.members.all())
    r1, r2 = members[0], (members[1] if len(members) > 1 else members[0])

    # Two annotations: r1 says "no", r2 says "yes". Mark r2's as authoritative.
    a1 = Annotation.objects.create(
        team=team, item=item, reviewer=r1, data={"verdict": "no"}, status=AnnotationStatus.SUBMITTED
    )
    if r2 != r1:
        a2 = Annotation.objects.create(
            team=team, item=item, reviewer=r2, data={"verdict": "yes"}, status=AnnotationStatus.SUBMITTED
        )
        # Promote r2's annotation
        a1.is_authoritative = False
        a1.save(update_fields=["is_authoritative"])
        a2.is_authoritative = True
        a2.save(update_fields=["is_authoritative"])

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id})
    assert response.status_code == 200
    # Even though r1 disagreed, the authoritative r2 agrees with the judge.
    body = response.content.decode()
    if r2 != r1:
        assert "Agreement: 1 / 1" in body


@pytest.mark.django_db()
def test_concordance_view_404_for_cross_team_config(authed_client, concordance_flag):
    client, _, _ = authed_client
    other_team = TeamWithUsersFactory.create()
    other_eval = EvaluationConfigFactory.create(team=other_team)
    other_queue = AnnotationQueueFactory.create(team=other_team)

    url = reverse("assessments:concordance", args=[other_team.slug])
    response = client.get(url, {"eval": other_eval.id, "queue": other_queue.id})
    # The user is not a member of other_team, so the team-required decorator blocks them.
    assert response.status_code in (302, 403, 404)


@pytest.mark.django_db()
def test_concordance_view_returns_404_when_flag_off(authed_client, db):
    client, team, _ = authed_client
    # Explicitly disable the flag for this test.
    flag, _ = Flag.objects.get_or_create(name="flag_assessments_concordance")
    flag.everyone = False
    flag.save()
    flag.flush()

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_concordance_view_renders_breadcrumbs(authed_client, concordance_flag):
    client, team, _ = authed_client
    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url)
    body = response.content.decode()
    # Breadcrumb structure: Evaluations link + active Concordance crumb
    assert 'aria-label="breadcrumbs"' in body
    assert reverse("evaluations:home", args=[team.slug]) in body


@pytest.mark.django_db()
def test_concordance_view_renders_agreement_percentage(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id})
    # 1 agree / 2 matched = 50%
    assert "Agreement: 1 / 2 (50%)" in response.content.decode()


@pytest.mark.django_db()
def test_concordance_view_show_eval_only_lists_unmatched_eval_sessions(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id, "show": "eval_only"})
    body = response.content.decode()
    # Eval-only count is still 1 (from the fixture); the table now shows that row.
    assert "Eval only: 1" in body
    # Eval-only rows have a judge value but no human value (rendered as em-dash).
    # The row's "Agree?" column also renders em-dash for non-matched rows.
    assert body.count("<td>—</td>") >= 1


@pytest.mark.django_db()
def test_concordance_view_show_human_only_lists_unmatched_human_sessions(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id, "show": "human_only"})
    body = response.content.decode()
    assert "Human only: 1" in body


@pytest.mark.django_db()
def test_concordance_view_show_all_renders_every_bucket(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id, "show": "all"})
    body = response.content.decode()
    # Body still reports the summary counts.
    assert "Matched: 2" in body
    assert "Eval only: 1" in body
    assert "Human only: 1" in body


@pytest.mark.django_db()
def test_concordance_view_renders_deep_links_per_row(authed_client, concordance_flag):
    client, team, _ = authed_client
    eval_config, queue = _build_concordance_fixtures(team)

    url = reverse("assessments:concordance", args=[team.slug])
    response = client.get(url, {"eval": eval_config.id, "queue": queue.id, "show": "all"})
    body = response.content.decode()
    # At least one annotate_item URL for the queue + one evaluation_results URL for the eval config.
    assert f"/queue/{queue.id}/item/" in body
    assert f"/evaluations/{eval_config.id}/evaluation_runs/" in body
    # Session links use the experiment public_id + session external_id; sanity-check by counting
    # session-detail link occurrences. The "all" view has 4 sessions (2 matched + 1 eval-only + 1 human-only).
    assert body.count("/messages/") >= 4

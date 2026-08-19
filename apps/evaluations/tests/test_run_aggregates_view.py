"""The results page must not present a run mid-finalization as one with no aggregates.

A run's completion side effects run as their own task after the status commits (ADR-0047),
so the page can load — and does, because the completing tick publishes the poller's stop
signal — before the aggregates exist. `finalized_at` is what separates "still coming" from
"this run produced none", and the aggregates block polls itself until it is set — or until the
aggregates themselves show up, whichever comes first.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.evaluations.const import FINALIZATION_GRACE
from apps.evaluations.models import EvaluationRun, EvaluationRunStatus
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationRunAggregateFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory


@pytest.fixture()
def membership(db):
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationRun),
        codename="view_evaluationrun",
    )
    group = GroupFactory.create(name="evaluations-view-only")
    group.permissions.add(view_perm)
    return MembershipFactory.create(groups=[group])


@pytest.fixture()
def client_with_user(membership):
    client = Client()
    client.force_login(membership.user)
    return client


def _completed_run(team, *, finalized: bool, finished_ago=timedelta(seconds=5)) -> EvaluationRun:
    finished_at = timezone.now() - finished_ago
    return EvaluationRunFactory.create(
        config=EvaluationConfigFactory.create(team=team),
        team=team,
        status=EvaluationRunStatus.COMPLETED,
        finished_at=finished_at,
        finalized_at=finished_at if finalized else None,
    )


def _aggregates_url(run) -> str:
    return reverse("evaluations:evaluation_run_aggregates", args=[run.team.slug, run.config_id, run.id])


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("status", "finalized", "finished_ago", "expected"),
    [
        pytest.param(EvaluationRunStatus.COMPLETED, False, timedelta(seconds=5), True, id="completed-unfinalized"),
        pytest.param(EvaluationRunStatus.COMPLETED, True, timedelta(seconds=5), False, id="completed-finalized"),
        pytest.param(
            EvaluationRunStatus.COMPLETED,
            False,
            FINALIZATION_GRACE + timedelta(minutes=1),
            False,
            id="grace-window-expired",
        ),
        pytest.param(EvaluationRunStatus.PROCESSING, False, timedelta(seconds=5), False, id="still-processing"),
        pytest.param(EvaluationRunStatus.FAILED, False, timedelta(seconds=5), False, id="failed"),
    ],
)
def test_is_finalizing(membership, status, finalized, finished_ago, expected):
    """Nothing retries a lost finalization, so the wait is bounded by the grace window."""
    finished_at = timezone.now() - finished_ago
    run = EvaluationRunFactory.create(
        team=membership.team,
        status=status,
        finished_at=finished_at,
        finalized_at=finished_at if finalized else None,
    )

    assert run.is_finalizing is expected


@pytest.mark.django_db()
def test_unfinalized_run_shows_a_computing_state_that_polls(client_with_user, membership):
    run = _completed_run(membership.team, finalized=False)

    response = client_with_user.get(_aggregates_url(run))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Computing aggregates" in content
    assert f'hx-get="{_aggregates_url(run)}"' in content  # keeps polling


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "finalized",
    [
        pytest.param(True, id="finalized"),
        # A finalization that computed the aggregates and then died before stamping, or an old-code
        # worker that never stamps at all. Nothing retries either, so waiting on the marker would
        # spin over results the run already has.
        pytest.param(False, id="unstamped-but-aggregates-landed"),
    ],
)
def test_run_with_aggregates_renders_them_and_stops_polling(client_with_user, membership, finalized):
    run = _completed_run(membership.team, finalized=finalized)
    EvaluationRunAggregateFactory.create(
        run=run,
        evaluator=EvaluatorFactory.create(team=membership.team, name="Sentiment"),
        aggregates={"score": {"type": "numeric", "count": 3, "mean": 2, "median": 2, "min": 1, "max": 3}},
    )

    response = client_with_user.get(_aggregates_url(run))

    content = response.content.decode()
    assert "Sentiment" in content
    assert "Computing aggregates" not in content
    assert "hx-get" not in content


@pytest.mark.django_db()
def test_binary_aggregate_renders_a_count_for_each_label(client_with_user, membership):
    run = _completed_run(membership.team, finalized=True)
    EvaluationRunAggregateFactory.create(
        run=run,
        evaluator=EvaluatorFactory.create(team=membership.team, binary_schema=True),
        aggregates={"correct": {"type": "binary", "count": 4, "mean": 0.75, "true_count": 3}},
    )

    response = client_with_user.get(_aggregates_url(run))

    content = response.content.decode()
    assert "3/4" in content
    assert "Incorrect:" in content
    assert "1/4" in content


@pytest.mark.django_db()
def test_finalized_run_with_no_aggregates_renders_nothing(client_with_user, membership):
    """A run whose results were all errors legitimately has none — and must not poll for them."""
    run = _completed_run(membership.team, finalized=True)

    response = client_with_user.get(_aggregates_url(run))

    content = response.content.decode()
    assert "Aggregates" not in content
    assert "Computing aggregates" not in content
    assert "hx-get" not in content


@pytest.mark.django_db()
def test_results_page_embeds_the_computing_state(client_with_user, membership):
    """The regression this guards: the reloaded page rendered no aggregates section at all."""
    run = _completed_run(membership.team, finalized=False)

    url = reverse("evaluations:evaluation_results_home", args=[run.team.slug, run.config_id, run.id])
    response = client_with_user.get(url)

    assert response.status_code == 200
    assert "Computing aggregates" in response.content.decode()


@pytest.mark.django_db()
def test_another_teams_run_is_not_reachable(client_with_user):
    other_run = _completed_run(MembershipFactory.create().team, finalized=False)

    response = client_with_user.get(_aggregates_url(other_run))

    assert response.status_code == 404

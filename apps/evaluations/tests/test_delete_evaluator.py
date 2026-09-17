from unittest.mock import patch

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus, Evaluator
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationResultFactory,
    EvaluationRunAggregateFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_delete_evaluator(client, team_with_users):
    """Hard-deleting an evaluator with no history returns an empty 200 body, letting the row disappear."""
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert response.content == b""
    assert not Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_delete_evaluator_without_delete_perm_is_forbidden(client, team_with_users):
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(Evaluator),
        codename="view_evaluator",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    evaluator = EvaluatorFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 403
    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(EvaluationRunStatus.PENDING, id="pending"),
        pytest.param(EvaluationRunStatus.PROCESSING, id="processing"),
    ],
)
def test_delete_evaluator_blocked_while_run_in_flight(status, client, team_with_users):
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    EvaluationRunFactory.create(team=team_with_users, config=config, status=status)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 409
    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_delete_evaluator_allowed_when_runs_terminal(client, team_with_users):
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_delete_evaluator_allowed_when_on_no_config(client, team_with_users):
    """An evaluator on no config has no related runs and deletes cleanly."""
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert not Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_delete_evaluator_blocked_when_shared_config_in_flight(client, team_with_users):
    """Evaluator on two configs: A in-flight, B terminal -> blocked (would corrupt A)."""
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config_a = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    config_b = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    EvaluationRunFactory.create(team=team_with_users, config=config_a, status=EvaluationRunStatus.PROCESSING)
    EvaluationRunFactory.create(team=team_with_users, config=config_b, status=EvaluationRunStatus.COMPLETED)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 409
    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
def test_delete_evaluator_for_other_team_returns_404(client, team_with_users):
    user = team_with_users.members.first()
    other_team = TeamFactory.create()
    evaluator = EvaluatorFactory.create(team=other_team)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 404
    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(EvaluationRunStatus.PENDING, id="pending"),
        pytest.param(EvaluationRunStatus.PROCESSING, id="processing"),
    ],
)
def test_delete_evaluator_blocked_by_frozen_plan_after_config_removal(status, client, team_with_users):
    """A run holds its evaluator in evaluator_ids even after the config drops it."""
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    EvaluationRunFactory.create(
        team=team_with_users,
        config=config,
        status=status,
        evaluator_ids=[evaluator.id],
    )
    config.evaluators.clear()

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 409
    assert Evaluator.objects.filter(id=evaluator.id).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "history",
    [
        pytest.param("result", id="results-only"),
        pytest.param("aggregate", id="aggregates-only"),
        pytest.param("both", id="results-and-aggregates"),
    ],
)
def test_delete_evaluator_with_any_history_archives_it(history, client, team_with_users):
    """An aggregates-only evaluator must archive, not hard-delete, or PROTECT raises; the row re-renders as archived."""
    evaluator = EvaluatorFactory.create(team=team_with_users, name="Scorer")
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
    if history in ("result", "both"):
        EvaluationResultFactory.create(team=team_with_users, run=run, evaluator=evaluator)
    if history in ("aggregate", "both"):
        EvaluationRunAggregateFactory.create(run=run, evaluator=evaluator)

    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 200
    evaluator.refresh_from_db()
    assert evaluator.is_archived is True
    html = response.content.decode()
    assert "Scorer" in html
    assert "Archived" in html
    assert reverse("evaluations:evaluator_unarchive", args=[team_with_users.slug, evaluator.id]) in html


@pytest.mark.django_db()
def test_unarchive_restores_the_evaluator(client, team_with_users):
    """Unarchiving an archived evaluator clears its is_archived flag and returns the row without the Archived badge."""
    evaluator = EvaluatorFactory.create(team=team_with_users, name="Scorer")
    evaluator.archive()

    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluator_unarchive", args=[team_with_users.slug, evaluator.id])
    response = client.post(url)

    assert response.status_code == 200
    evaluator.refresh_from_db()
    assert evaluator.is_archived is False
    html = response.content.decode()
    assert "Scorer" in html
    assert "Archived" not in html
    assert reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id]) in html


@pytest.mark.django_db()
def test_unarchive_without_delete_perm_is_forbidden(client, team_with_users):
    """A user without delete_evaluator permission cannot unarchive an evaluator."""
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(Evaluator),
        codename="view_evaluator",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only-unarchive")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    evaluator = EvaluatorFactory.create(team=team_with_users)
    evaluator.archive()

    client.force_login(membership.user)
    url = reverse("evaluations:evaluator_unarchive", args=[team_with_users.slug, evaluator.id])
    response = client.post(url)

    assert response.status_code == 403
    evaluator.refresh_from_db()
    assert evaluator.is_archived is True


@pytest.mark.django_db()
def test_delete_evaluator_archives_when_a_result_lands_after_the_history_check(client, team_with_users):
    """A result written between the history check and the delete makes PROTECT refuse; the view archives instead."""
    evaluator = EvaluatorFactory.create(team=team_with_users, name="Scorer")
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)

    def result_lands_then_delete(self, *args, **kwargs):
        EvaluationResultFactory.create(team=team_with_users, run=run, evaluator=evaluator)
        return original_delete(self, *args, **kwargs)

    original_delete = Evaluator.delete
    client.force_login(team_with_users.members.first())
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    with patch.object(Evaluator, "delete", result_lands_then_delete):
        response = client.delete(url)

    assert response.status_code == 200
    evaluator.refresh_from_db()
    assert evaluator.is_archived is True
    assert "Archived" in response.content.decode()

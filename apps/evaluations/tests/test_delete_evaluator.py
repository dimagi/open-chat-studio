import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import EvaluationRunStatus, Evaluator
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_delete_evaluator(client, team_with_users):
    user = team_with_users.members.first()
    evaluator = EvaluatorFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 200
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

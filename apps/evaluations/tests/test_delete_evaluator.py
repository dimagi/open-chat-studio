import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import Evaluator
from apps.utils.factories.evaluations import EvaluatorFactory
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
def test_delete_evaluator_for_other_team_returns_404(client, team_with_users):
    user = team_with_users.members.first()
    other_team = TeamFactory.create()
    evaluator = EvaluatorFactory.create(team=other_team)

    client.force_login(user)
    url = reverse("evaluations:evaluator_delete", args=[team_with_users.slug, evaluator.id])
    response = client.delete(url)

    assert response.status_code == 404
    assert Evaluator.objects.filter(id=evaluator.id).exists()

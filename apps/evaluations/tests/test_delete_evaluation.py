import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.evaluations.models import EvaluationConfig, EvaluationRun
from apps.utils.factories.evaluations import EvaluationConfigFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import GroupFactory


@pytest.mark.django_db()
def test_delete_evaluation_without_redirect_param(client, team_with_users):
    """List-table delete: no HX-Redirect, row removal handled by HTMX swap."""
    user = team_with_users.members.first()
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    response = client.delete(url)

    assert response.status_code == 200
    assert "HX-Redirect" not in response.headers
    assert not EvaluationConfig.objects.filter(id=evaluation.id).exists()


@pytest.mark.django_db()
def test_delete_evaluation_with_redirect_param(client, team_with_users):
    """Detail-page delete: HX-Redirect to the eval list."""
    user = team_with_users.members.first()
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    response = client.delete(f"{url}?redirect=1")

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == reverse("evaluations:home", args=[team_with_users.slug])
    assert not EvaluationConfig.objects.filter(id=evaluation.id).exists()


@pytest.mark.django_db()
def test_delete_evaluation_redirect_param_zero_does_not_redirect(client, team_with_users):
    """Strict contract: only ?redirect=1 sets HX-Redirect; ?redirect=0 must not."""
    user = team_with_users.members.first()
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    response = client.delete(f"{url}?redirect=0")

    assert response.status_code == 200
    assert "HX-Redirect" not in response.headers
    assert not EvaluationConfig.objects.filter(id=evaluation.id).exists()


@pytest.mark.django_db()
def test_delete_evaluation_for_other_team_returns_404(client, team_with_users):
    user = team_with_users.members.first()
    other_team = TeamFactory.create()
    evaluation = EvaluationConfigFactory.create(team=other_team)

    client.force_login(user)
    url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    response = client.delete(url)

    assert response.status_code == 404
    assert EvaluationConfig.objects.filter(id=evaluation.id).exists()


@pytest.mark.django_db()
def test_detail_page_shows_edit_and_delete_for_admin(client, team_with_users):
    """Detail page renders both an Edit link and a Delete button with ?redirect=1."""
    user = team_with_users.members.first()
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(user)
    url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, evaluation.id])
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    edit_url = reverse("evaluations:edit", args=[team_with_users.slug, evaluation.id])
    delete_url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    assert f'href="{edit_url}"' in content
    assert f"{delete_url}?redirect=1" in content


@pytest.mark.django_db()
def test_detail_page_hides_delete_for_user_without_delete_perm(client, team_with_users):
    """A team member with view-only perms must not see the Delete control."""
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationRun),
        codename="view_evaluationrun",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, evaluation.id])
    response = client.get(url)

    assert response.status_code == 200
    delete_url = reverse("evaluations:delete", args=[team_with_users.slug, evaluation.id])
    assert delete_url not in response.content.decode()

    delete_response = client.delete(delete_url)
    assert delete_response.status_code == 403
    assert EvaluationConfig.objects.filter(id=evaluation.id).exists()


@pytest.mark.django_db()
def test_detail_page_hides_edit_for_user_without_change_perm(client, team_with_users):
    """A team member with view-only perms must not see the Edit control."""
    view_perm = Permission.objects.get(
        content_type=ContentType.objects.get_for_model(EvaluationRun),
        codename="view_evaluationrun",
    )
    limited_group = GroupFactory.create(name="evaluations-view-only")
    limited_group.permissions.add(view_perm)
    membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
    evaluation = EvaluationConfigFactory.create(team=team_with_users)

    client.force_login(membership.user)
    url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, evaluation.id])
    response = client.get(url)

    assert response.status_code == 200
    edit_url = reverse("evaluations:edit", args=[team_with_users.slug, evaluation.id])
    assert f'href="{edit_url}"' not in response.content.decode()

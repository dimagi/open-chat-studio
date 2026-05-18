import pytest
from django.urls import reverse

from apps.evaluations.models import EvaluationConfig
from apps.utils.factories.evaluations import EvaluationConfigFactory


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

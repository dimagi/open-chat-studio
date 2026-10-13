"""Tests for the evaluator Copy action: POST duplicates the evaluator and redirects to the copy's edit page."""

import pytest
from django.urls import reverse

from apps.evaluations.models import Evaluator
from apps.evaluations.tests.conftest import INSUFFICIENT_COPY_PERMISSIONS
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_copy_creates_the_duplicate_and_redirects_to_its_edit_page(client, team_with_users):
    """POST creates the copy and answers with an HX-Redirect to its edit page."""
    source = EvaluatorFactory.create(team=team_with_users, name="Sentiment scorer")

    client.force_login(team_with_users.members.first())
    response = client.post(reverse("evaluations:evaluator_copy", args=[team_with_users.slug, source.id]))

    duplicate = Evaluator.objects.get(team=team_with_users, name="Sentiment scorer (copy)")
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == reverse(
        "evaluations:evaluator_edit", args=[team_with_users.slug, duplicate.id]
    )


@pytest.mark.django_db()
def test_copy_of_another_teams_evaluator_is_not_found(client, team_with_users):
    """An evaluator from another team is a 404 and nothing is created."""
    other_team = TeamFactory.create()
    source = EvaluatorFactory.create(team=other_team)

    client.force_login(team_with_users.members.first())
    response = client.post(reverse("evaluations:evaluator_copy", args=[team_with_users.slug, source.id]))

    assert response.status_code == 404
    assert Evaluator.objects.count() == 1


@pytest.mark.parametrize("codenames", INSUFFICIENT_COPY_PERMISSIONS)
@pytest.mark.django_db()
def test_copy_without_add_and_change_permissions_is_forbidden(client, team_with_users, evaluator_member, codenames):
    """A user who could not open the copy afterwards is not allowed to create it."""
    membership = evaluator_member(*codenames)
    source = EvaluatorFactory.create(team=team_with_users)

    client.force_login(membership.user)
    response = client.post(reverse("evaluations:evaluator_copy", args=[team_with_users.slug, source.id]))

    assert response.status_code == 403
    assert Evaluator.objects.count() == 1


@pytest.mark.django_db()
def test_copy_rejects_get(client, team_with_users):
    """The copy endpoint only accepts POST."""
    source = EvaluatorFactory.create(team=team_with_users)

    client.force_login(team_with_users.members.first())
    response = client.get(reverse("evaluations:evaluator_copy", args=[team_with_users.slug, source.id]))

    assert response.status_code == 405
    assert Evaluator.objects.count() == 1

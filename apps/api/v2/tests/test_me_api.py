import pytest
from allauth.account.models import EmailAddress
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


def test_me_url_reverses():
    assert reverse("api:v2:me") == "/api/v2/me/"


def test_me_url_resolves():
    match = resolve("/api/v2/me/")
    assert match.url_name == "me"


@pytest.mark.django_db()
def test_me_unauthenticated():
    client = APIClient()
    response = client.get(reverse("api:v2:me"))
    assert response.status_code == 401


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_me_returns_user_and_team(auth_method):
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    client = ApiTestClient(user, team, auth_method=auth_method)

    response = client.get(reverse("api:v2:me"))

    assert response.status_code == 200
    data = response.json()

    # User fields
    assert data["id"] == user.id
    assert data["username"] == user.username
    assert data["email"] == user.email
    assert data["first_name"] == user.first_name
    assert data["last_name"] == user.last_name

    # Email verified (no EmailAddress record → False)
    assert data["email_verified"] is False

    # Team scoped to this token
    assert data["team"]["name"] == team.name
    assert data["team"]["slug"] == team.slug


@pytest.mark.django_db()
def test_me_email_verified_true():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

    client = ApiTestClient(user, team)
    response = client.get(reverse("api:v2:me"))

    assert response.status_code == 200
    assert response.json()["email_verified"] is True


@pytest.mark.django_db()
def test_me_email_verified_false_when_unverified():
    team = TeamWithUsersFactory.create()
    user = team.members.first()
    EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)

    client = ApiTestClient(user, team)
    response = client.get(reverse("api:v2:me"))

    assert response.status_code == 200
    assert response.json()["email_verified"] is False


@pytest.mark.django_db()
def test_me_team_is_scoped_to_token():
    """A user belonging to two teams only sees the team their token is scoped to."""
    team_a = TeamWithUsersFactory.create()
    user = team_a.members.first()
    # Add user to a second team
    team_b = TeamWithUsersFactory.create()
    MembershipFactory.create(user=user, team=team_b)

    client = ApiTestClient(user, team_a)
    response = client.get(reverse("api:v2:me"))

    assert response.status_code == 200
    assert response.json()["team"]["slug"] == team_a.slug

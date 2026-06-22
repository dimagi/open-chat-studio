import pytest
from django.urls import resolve, reverse
from rest_framework.test import APIClient

from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

pytestmark = pytest.mark.django_db


def _admin(team):
    return next(m.user for m in team.membership_set.all() if m.is_team_admin())


def _non_admin(team):
    return next(m.user for m in team.membership_set.all() if not m.is_team_admin())


def test_manifest_url_resolves():
    assert reverse("api:v2:manifest") == "/api/v2/manifest/"
    assert resolve("/api/v2/manifest/").url_name == "manifest"


def test_manifest_requires_authentication():
    assert APIClient().get(reverse("api:v2:manifest")).status_code == 401


def test_manifest_rejects_non_admin():
    team = TeamWithUsersFactory()
    client = ApiTestClient(_non_admin(team), team)
    assert client.get(reverse("api:v2:manifest")).status_code == 403


def test_manifest_returns_entries_for_admin():
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    response = client.get(reverse("api:v2:manifest"))
    assert response.status_code == 200
    body = response.json()
    assert "schema_checksum" in body
    assert any(e["resource"] == "teams" and e["model"] == "teams.team" for e in body["entries"])

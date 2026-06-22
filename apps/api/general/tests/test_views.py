import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.experiments.models import Participant
from apps.teams.sync import seal as seal_mod
from apps.utils.factories.experiment import ConsentFormFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

pytestmark = pytest.mark.django_db


def _admin(team):
    return next(m.user for m in team.membership_set.all() if m.is_team_admin())


def _non_admin(team):
    return next(m.user for m in team.membership_set.all() if not m.is_team_admin())


def _resource_url(resource):
    return reverse("api:v2:resource", kwargs={"resource": resource})


def test_manifest_url_resolves():
    assert reverse("api:v2:manifest") == "/api/v2/manifest/"
    assert resolve("/api/v2/manifest/").url_name == "manifest"


def test_resource_url_resolves():
    assert _resource_url("teams") == "/api/v2/teams/"


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


def test_resource_returns_team_scoped_rows():
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    response = client.get(_resource_url("teams"))
    assert response.status_code == 200
    body = response.json()
    assert [r["id"] for r in body["results"]] == [team.id]
    assert body["has_more"] is False


def test_resource_rejects_unlisted_model():
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    assert client.get(_resource_url("assistant")).status_code == 404


def test_resource_isolates_other_teams_data():
    team = TeamWithUsersFactory()
    other = TeamWithUsersFactory()
    mine = LlmProviderModelFactory(team=team)
    theirs = LlmProviderModelFactory(team=other)
    client = ApiTestClient(_admin(team), team)
    ids = [r["id"] for r in client.get(_resource_url("llm_provider_model")).json()["results"]]
    assert mine.id in ids
    assert theirs.id not in ids


def test_secret_resource_fails_closed_without_public_key():
    team = TeamWithUsersFactory()
    LlmProviderFactory(team=team)
    client = ApiTestClient(_admin(team), team)
    assert client.get(_resource_url("llm_provider")).status_code == 409


def test_secret_resource_seals_config_with_team_public_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    team = TeamWithUsersFactory(public_key=public_pem.decode())
    LlmProviderFactory(team=team, config={"key": "sk-xyz"})
    client = ApiTestClient(_admin(team), team)
    response = client.get(_resource_url("llm_provider"))
    assert response.status_code == 200
    sealed = response.json()["results"][0]["config"]
    assert seal_mod.unseal(sealed, private) == {"key": "sk-xyz"}


def test_pk_pagination_advances_via_cursor():
    team = TeamWithUsersFactory()
    c1 = ConsentFormFactory(team=team)
    c2 = ConsentFormFactory(team=team)
    client = ApiTestClient(_admin(team), team)
    resource = "consent_form"

    collected, cursor = [], None
    for _ in range(10):
        page = client.get(_resource_url(resource), {"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        collected += [r["id"] for r in page["results"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break

    assert {c1.id, c2.id} <= set(collected)
    assert len(collected) == len(set(collected))  # no row served twice


def test_updated_at_id_cursor_pages_ties_without_skip_or_repeat():
    team = TeamWithUsersFactory()
    parts = [ParticipantFactory(team=team) for _ in range(3)]
    shared = timezone.now()
    Participant.objects.filter(id__in=[p.id for p in parts]).update(updated_at=shared)
    client = ApiTestClient(_admin(team), team)
    resource = "participant"

    collected, cursor = [], None
    for _ in range(10):
        page = client.get(_resource_url(resource), {"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        collected += [r["id"] for r in page["results"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break

    assert sorted(collected) == sorted(p.id for p in parts)  # each tied row exactly once

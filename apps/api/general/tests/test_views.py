import base64
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.experiments.models import Participant
from apps.pipelines.models import Pipeline
from apps.teams.export import seal as seal_mod
from apps.teams.models import Membership
from apps.users.models import CustomUser
from apps.utils.factories.experiment import ConsentFormFactory, ParticipantFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

pytestmark = pytest.mark.django_db


def _admin(team):
    return next(m.user for m in team.membership_set.all() if m.is_team_admin())


def _non_admin(team):
    return next(m.user for m in team.membership_set.all() if not m.is_team_admin())


def _resource_url(resource):
    return reverse(f"api:v2:resource-{resource}")


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
    # Only manifested resources are routed, so an unlisted model 404s at routing.
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    assert client.get("/api/v2/assistant/").status_code == 404


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


def _b64(payload):
    return base64.b64encode(payload).decode()


@pytest.mark.parametrize(
    ("resource", "params"),
    [
        pytest.param("consent_form", {"cursor": "abc"}, id="non-numeric-pk-cursor"),
        pytest.param("consent_form", {"cursor": "1.5"}, id="float-pk-cursor"),
        pytest.param("participant", {"cursor": "!!!not-base64"}, id="non-base64-keyset-cursor"),
        pytest.param("participant", {"cursor": _b64(b"not json")}, id="non-json-keyset-cursor"),
        pytest.param("participant", {"cursor": _b64(json.dumps({"id": 1}).encode())}, id="keyset-cursor-missing-key"),
        pytest.param("consent_form", {"limit": "abc"}, id="non-integer-limit"),
        pytest.param("consent_form", {"limit": "-5"}, id="negative-limit"),
        pytest.param("consent_form", {"limit": "0"}, id="zero-limit"),
    ],
)
def test_malformed_pagination_input_returns_400(resource, params):
    """Bad cursors and limits are client errors (400), not 500s -- a 500 makes the export client
    treat the request as transient and retry it in a pointless storm."""
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    assert client.get(_resource_url(resource), params).status_code == 400


def _add_membership_with_group(team, i):
    user = CustomUser.objects.create(username=f"member-{i}@example.com", email=f"member-{i}@example.com")
    membership = Membership.objects.create(team=team, user=user)
    membership.groups.add(Group.objects.create(name=f"grp-{team.id}-{i}"))


def test_membership_export_does_not_query_groups_per_row():
    """Exporting memberships must fetch their group names with a prefetch, not one query per row --
    otherwise a 1000-row page issues ~1000 extra queries."""
    team = TeamWithUsersFactory()
    client = ApiTestClient(_admin(team), team)
    for i in range(2):
        _add_membership_with_group(team, i)
    with CaptureQueriesContext(connection) as few:
        assert client.get(_resource_url("membership"), {"limit": 1000}).status_code == 200

    for i in range(2, 7):
        _add_membership_with_group(team, i)
    with CaptureQueriesContext(connection) as many:
        assert client.get(_resource_url("membership"), {"limit": 1000}).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries)  # query count is flat in row count


def test_working_version_always_served_before_its_published_copies():
    """Versioned resources page by pk, and a working version is always created before its published
    copies -- so it has a lower id, is served first, and the self-referential working_version FK
    resolves on import. The newest updated_at is forced onto the working versions to prove the order
    follows id, not timestamps. Paging must serve each row exactly once. This guards the implicit
    pk-order invariant: if a published version ever got a lower id, this fails instead of the sync
    silently nulling the FK."""
    team = TeamWithUsersFactory()
    working_a = PipelineFactory(team=team)
    published_a1 = working_a.create_new_version()
    published_a2 = working_a.create_new_version()
    working_b = PipelineFactory(team=team)
    published_b1 = working_b.create_new_version()
    # Force the working versions to be the most-recently-updated, so any updated_at-based ordering
    # would wrongly serve them last -- only id ordering keeps them first.
    Pipeline.objects.filter(id__in=[working_a.id, working_b.id]).update(updated_at=timezone.now() + timedelta(days=1))
    client = ApiTestClient(_admin(team), team)

    collected, cursor = [], None
    for _ in range(20):
        page = client.get(_resource_url("pipeline"), {"limit": 1, **({"cursor": cursor} if cursor else {})}).json()
        collected += [r["id"] for r in page["results"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break

    every_id = [working_a.id, published_a1.id, published_a2.id, working_b.id, published_b1.id]
    assert sorted(collected) == sorted(every_id)  # every row served exactly once
    assert len(collected) == len(set(collected))  # none served twice
    for working, published in [(working_a, published_a1), (working_a, published_a2), (working_b, published_b1)]:
        assert collected.index(working.id) < collected.index(published.id)


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

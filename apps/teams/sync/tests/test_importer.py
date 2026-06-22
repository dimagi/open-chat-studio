from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group

from apps.experiments.models import ConsentForm
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.models import Membership, Team
from apps.teams.sync import seal as seal_mod
from apps.teams.sync.importer import Importer
from apps.teams.sync.translation import FKTranslationStore
from apps.users.models import CustomUser


def _user_row(source_id, username, **extra):
    return {
        "id": source_id,
        "username": username,
        "email": username,
        "first_name": "",
        "last_name": "",
        "is_active": True,
        "is_staff": False,
        "is_superuser": False,
        "date_joined": PAST,
        "last_login": None,
        **extra,
    }


pytestmark = pytest.mark.django_db

PAST = "2020-01-02T03:04:05+00:00"


@pytest.fixture()
def store(tmp_path):
    return FKTranslationStore(tmp_path / "team.sqlite")


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private


def _team_row(source_id=9001, slug="imported-team-xyz"):
    return {
        "id": source_id,
        "name": "Imported",
        "slug": slug,
        "feature_flags": [],
        "created_at": PAST,
        "updated_at": PAST,
    }


def test_import_team_creates_row_records_translation_and_restores_timestamps(store):
    Importer(store).import_rows("teams.team", [_team_row()])

    target_pk = store.get_target("teams.team", 9001)
    team = Team.objects.get(pk=target_pk)
    assert team.slug == "imported-team-xyz"
    assert team.created_at == datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_import_resolves_fk_to_target_pk_and_unseals_secret(store, keypair):
    public_key, private_key = keypair
    importer = Importer(store, private_key=private_key)
    importer.import_rows("teams.team", [_team_row()])
    target_team = store.get_target("teams.team", 9001)

    provider_row = {
        "id": 5,
        "name": "OpenAI",
        "type": "openai",
        "config": seal_mod.seal({"api_key": "sk-x"}, public_key),
        "team": 9001,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("service_providers.llmprovider", [provider_row])

    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == target_team
    assert provider.config == {"api_key": "sk-x"}


def test_rerun_does_not_duplicate(store):
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    first_pk = store.get_target("teams.team", 9001)
    importer.import_rows("teams.team", [_team_row()])
    assert store.get_target("teams.team", 9001) == first_pk
    assert Team.objects.filter(slug="imported-team-xyz").count() == 1


def test_global_row_matches_existing_and_is_not_recreated(store):
    existing = LlmProviderModel.objects.create(team=None, type="openai", name="gpt-glob", max_token_limit=8192)
    count_before = LlmProviderModel.objects.count()
    row = {
        "id": 77,
        "team": None,
        "type": "openai",
        "name": "gpt-glob",
        "max_token_limit": 8192,
        "deprecated": False,
        "created_at": PAST,
        "updated_at": PAST,
    }

    Importer(store).import_rows("service_providers.llmprovidermodel", [row])

    assert store.get_target("service_providers.llmprovidermodel", 77) == existing.pk
    assert LlmProviderModel.objects.count() == count_before


def test_node_params_and_fk_columns_are_remapped(store):
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    importer.import_rows(
        "service_providers.llmprovider",
        [{"id": 7, "name": "P", "type": "openai", "config": {}, "team": 9001, "created_at": PAST, "updated_at": PAST}],
    )
    provider_pk = store.get_target("service_providers.llmprovider", 7)
    importer.import_rows(
        "pipelines.pipeline",
        [
            {
                "id": 100,
                "name": "Flow",
                "team": 9001,
                "data": {"nodes": [], "edges": []},
                "version_number": 1,
                "is_archived": False,
                "working_version": None,
                "created_at": PAST,
                "updated_at": PAST,
            }
        ],
    )
    pipeline_pk = store.get_target("pipelines.pipeline", 100)
    node_row = {
        "id": 200,
        "flow_id": "n1",
        "type": "LLMResponseWithPrompt",
        "label": "",
        "params": {"name": "n1", "llm_provider_id": 7},
        "llm_provider": 7,
        "pipeline": 100,
        "working_version": None,
        "is_archived": False,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("pipelines.node", [node_row])

    node = Node.objects.get(pk=store.get_target("pipelines.node", 200))
    assert node.pipeline_id == pipeline_pk
    assert node.llm_provider_id == provider_pk
    assert node.params["llm_provider_id"] == provider_pk
    assert Pipeline.objects.get(pk=pipeline_pk).team_id == team_pk


def test_membership_groups_are_linked_by_name(store):
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    user = CustomUser.objects.create(username="synced@example.com", email="synced@example.com")
    store.record("users.customuser", 50, user.id)
    Group.objects.get_or_create(name="Sync Role X")

    importer.import_rows(
        "teams.membership",
        [{"id": 60, "team": 9001, "user": 50, "groups": ["Sync Role X"], "created_at": PAST, "updated_at": PAST}],
    )

    membership = Membership.objects.get(pk=store.get_target("teams.membership", 60))
    assert list(membership.groups.values_list("name", flat=True)) == ["Sync Role X"]


def test_default_consent_form_maps_to_auto_created_default(store):
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    auto_default = ConsentForm.objects.get(team_id=team_pk, is_default=True)

    row = {
        "id": 300,
        "name": "Imported Consent",
        "consent_text": "Source consent text",
        "is_default": True,
        "team": 9001,
        "capture_identifier": True,
        "identifier_label": "Email Address",
        "identifier_type": "email",
        "confirmation_text": "ok",
        "is_archived": False,
        "working_version": None,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("experiments.consentform", [row])

    assert store.get_target("experiments.consentform", 300) == auto_default.pk
    auto_default.refresh_from_db()
    assert auto_default.consent_text == "Source consent text"
    assert ConsentForm.objects.filter(team_id=team_pk, is_default=True).count() == 1


def test_existing_user_matched_by_username_is_not_overwritten_or_emailed(store):
    existing = CustomUser.objects.create(username="op@example.com", email="op@example.com", first_name="Original")
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)

    importer.import_rows("users.customuser", [_user_row(50, "op@example.com", first_name="Source")])

    assert store.get_target("users.customuser", 50) == existing.pk
    existing.refresh_from_db()
    assert existing.first_name == "Original"  # an existing account is mapped, never overwritten
    assert created_users == []  # not newly added -> no reset email


def test_new_user_triggers_on_user_created(store):
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)

    importer.import_rows("users.customuser", [_user_row(51, "fresh@example.com")])

    assert [u.username for u in created_users] == ["fresh@example.com"]

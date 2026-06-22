import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import mail
from django.core.management.base import CommandError

from apps.api.general.serializers import build_sync_serializer
from apps.service_providers.models import LlmProvider
from apps.teams.management.commands.sync_team import run_sync
from apps.teams.models import Team
from apps.teams.sync import seal as seal_mod
from apps.teams.sync.importer import Importer
from apps.teams.sync.manifest import schema_checksum
from apps.teams.sync.translation import FKTranslationStore
from apps.utils.factories.service_provider_factories import LlmProviderFactory

pytestmark = pytest.mark.django_db

PAST = "2020-01-02T03:04:05+00:00"


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private


class FakeClient:
    def __init__(self, manifest, rows_by_resource):
        self.manifest = manifest
        self.rows_by_resource = rows_by_resource
        self.iter_calls = []

    def get_manifest(self):
        return self.manifest

    def iter_rows(self, resource, start_cursor=None, limit=100):
        self.iter_calls.append((resource, start_cursor))
        return list(self.rows_by_resource.get(resource, []))


def _manifest(entries, checksum=None):
    return {"schema_checksum": checksum if checksum is not None else schema_checksum(), "entries": entries}


def _scenario(public_key):
    entries = [
        {"model": "teams.team", "resource": "teams", "phase": "structural", "cursor": "pk", "secret": False},
        {
            "model": "service_providers.llmprovider",
            "resource": "llm_provider",
            "phase": "structural",
            "cursor": "pk",
            "secret": True,
        },
    ]
    rows = {
        "teams": [
            {
                "id": 9001,
                "name": "Imported",
                "slug": "imported-team-z",
                "feature_flags": [],
                "created_at": PAST,
                "updated_at": PAST,
            }
        ],
        "llm_provider": [
            {
                "id": 5,
                "name": "OpenAI",
                "type": "openai",
                "config": seal_mod.seal({"api_key": "sk-x"}, public_key),
                "team": 9001,
                "created_at": PAST,
                "updated_at": PAST,
            }
        ],
    }
    return _manifest(entries), rows


def test_schema_checksum_mismatch_aborts(tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + 1
    store = FKTranslationStore(tmp_path / "t.sqlite")
    with pytest.raises(CommandError, match="schema"):
        run_sync(FakeClient(manifest, rows), store, keypair[1])


def test_skip_schema_check_bypasses_mismatch(tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + 1
    store = FKTranslationStore(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1], enforce_schema=False)

    assert Team.objects.filter(slug="imported-team-z").exists()


def test_new_users_receive_a_password_reset_email(tmp_path, keypair):
    entries = [
        {"model": "teams.team", "resource": "teams", "phase": "structural", "cursor": "pk", "secret": False},
        {"model": "users.customuser", "resource": "user", "phase": "structural", "cursor": "pk", "secret": False},
    ]
    rows = {
        "teams": [
            {
                "id": 9001,
                "name": "T",
                "slug": "imported-team-z",
                "feature_flags": [],
                "created_at": PAST,
                "updated_at": PAST,
            }
        ],
        "user": [
            {
                "id": 50,
                "username": "added@example.com",
                "email": "added@example.com",
                "first_name": "",
                "last_name": "",
                "is_active": True,
                "is_staff": False,
                "is_superuser": False,
                "date_joined": PAST,
                "last_login": None,
            }
        ],
    }
    store = FKTranslationStore(tmp_path / "t.sqlite")
    mail.outbox.clear()

    run_sync(FakeClient(_manifest(entries), rows), store, keypair[1])

    assert any("added@example.com" in message.to for message in mail.outbox)


def test_run_sync_builds_team_and_resolves_secret_provider(tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    store = FKTranslationStore(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1])

    team = Team.objects.get(pk=store.get_target("teams.team", 9001))
    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == team.id
    assert provider.config == {"api_key": "sk-x"}
    assert store.has_unfilled_targets() is False


def test_rerun_is_a_no_op_and_resumes_from_derived_cursor(tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    store = FKTranslationStore(tmp_path / "t.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1])

    second = FakeClient(manifest, rows)
    run_sync(second, store, keypair[1])

    assert Team.objects.filter(slug="imported-team-z").count() == 1
    # the second run resumes each pk resource from its highest synced source key
    assert dict(second.iter_calls)["teams"] == "9001"
    assert dict(second.iter_calls)["llm_provider"] == "5"


def test_serialized_row_round_trips_through_importer(tmp_path, keypair):
    """The serializer's output is exactly what the importer consumes."""
    public_key, private_key = keypair
    provider = LlmProviderFactory(config={"api_key": "sk-live"})
    row = build_sync_serializer(LlmProvider)(provider, context={"public_key": public_key}).data

    store = FKTranslationStore(tmp_path / "t.sqlite")
    target_team = Team.objects.create(name="Target", slug="target-z")
    store.record("teams.team", provider.team_id, target_team.id)

    Importer(store, private_key=private_key).import_rows("service_providers.llmprovider", [dict(row)])

    imported = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", provider.id))
    assert imported.team_id == target_team.id
    assert imported.config == {"api_key": "sk-live"}

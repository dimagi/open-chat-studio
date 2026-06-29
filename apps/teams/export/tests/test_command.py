import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import mail
from django.core.management.base import CommandError

from apps.api.export.serializers import build_resource_serializer
from apps.service_providers.models import LlmProvider
from apps.teams.export import seal as seal_mod
from apps.teams.export.importer import Importer
from apps.teams.export.manifest import schema_checksum
from apps.teams.export.translation import FKTranslationStore
from apps.teams.management.commands import sync_team
from apps.teams.management.commands.sync_team import Command, force_delete_team, run_sync
from apps.teams.models import Team
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

    def get_team(self):
        # The team is fetched on its own from the dedicated ``team/`` endpoint, not the manifest loop.
        return self.rows_by_resource["teams"][0]

    def iter_rows(self, resource, start_cursor=None, limit=100):
        self.iter_calls.append((resource, start_cursor))
        return list(self.rows_by_resource.get(resource, []))


def _manifest(entries, checksum=None):
    return {"schema_checksum": checksum if checksum is not None else schema_checksum(), "entries": entries}


def _scenario(public_key):
    entries = [
        {
            "model": "service_providers.llmprovider",
            "resource": "llm_provider",
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
    manifest["schema_checksum"] = manifest["schema_checksum"] + "-mismatch"
    store = FKTranslationStore(tmp_path / "t.sqlite")
    with pytest.raises(CommandError, match="schema"):
        run_sync(FakeClient(manifest, rows), store, keypair[1])


def test_aborts_when_secrets_present_but_no_private_key(tmp_path, keypair):
    """Without the private key the sealed secret fields would be imported as unreadable tokens, so
    the sync must refuse up front rather than silently corrupt provider configs / participant data."""
    manifest, rows = _scenario(keypair[0])
    store = FKTranslationStore(tmp_path / "t.sqlite")
    with pytest.raises(CommandError, match="private key"):
        run_sync(FakeClient(manifest, rows), store, None)

    assert not Team.objects.filter(slug="imported-team-z").exists()  # aborted before importing anything


def test_skip_schema_check_bypasses_mismatch(tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + "-mismatch"
    store = FKTranslationStore(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1], enforce_schema=False)

    assert Team.objects.filter(slug="imported-team-z").exists()


def test_new_users_receive_a_password_reset_email(tmp_path, keypair):
    entries = [
        {"model": "users.customuser", "resource": "user", "cursor": "pk", "secret": False},
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
    assert dict(second.iter_calls)["llm_provider"] == "5"


def test_force_delete_team_removes_team_and_resets_state(tmp_path):
    team = Team.objects.create(name="Old", slug="imported-team-z")
    state_db = tmp_path / "imported-team-z.sqlite"
    FKTranslationStore(state_db).record("teams.team", 9001, team.id)
    assert state_db.exists()
    mail.outbox.clear()

    force_delete_team("imported-team-z", tmp_path)

    assert not Team.objects.filter(slug="imported-team-z").exists()
    assert not state_db.exists()
    assert mail.outbox == []  # a re-import must not notify anyone that their team was deleted


def test_force_delete_team_is_a_no_op_when_team_missing(tmp_path):
    force_delete_team("never-synced", tmp_path)  # no team, no state DB -- must not raise


def test_force_delete_is_not_reached_when_preconditions_fail(tmp_path, keypair, monkeypatch):
    """A failing precondition (here, a schema mismatch) must abort before --force-delete runs, so a
    bad source can't leave the operator with no team."""
    Team.objects.create(name="Keep", slug="imported-team-z")
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + "-mismatch"
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: FakeClient(manifest, rows))

    options = {
        "source_url": "http://src",
        "api_key": "k",
        "team_slug": "imported-team-z",
        "private_key_path": None,
        "state_dir": str(tmp_path),
        "limit": 100,
        "skip_schema_check": False,
        "force_delete": True,
    }
    with pytest.raises(CommandError, match="schema"):
        Command().handle(**options)

    assert Team.objects.filter(slug="imported-team-z").exists()  # the destructive delete never ran


def _force_delete_options(tmp_path, **overrides):
    options = {
        "source_url": "http://src",
        "api_key": "k",
        "team_slug": "imported-team-z",
        "private_key_path": None,
        "state_dir": str(tmp_path),
        "limit": 100,
        "skip_schema_check": False,
        "force_delete": True,
        "interactive": True,
    }
    options.update(overrides)
    return options


def test_force_delete_aborts_when_confirmation_declined(tmp_path, monkeypatch):
    """An interactive --force-delete that isn't confirmed must abort before deleting anything."""
    Team.objects.create(name="Keep", slug="imported-team-z")
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: object())
    monkeypatch.setattr(sync_team, "check_sync_preconditions", lambda *a, **k: {})
    monkeypatch.setattr(sync_team, "run_sync", lambda *a, **k: pytest.fail("sync ran despite aborted delete"))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")

    with pytest.raises(CommandError, match="not confirmed"):
        Command().handle(**_force_delete_options(tmp_path))

    assert Team.objects.filter(slug="imported-team-z").exists()  # declined -> nothing deleted


def test_force_delete_noinput_skips_confirmation(tmp_path, monkeypatch):
    """--no-input deletes without prompting, for non-interactive (CI) runs."""
    Team.objects.create(name="Old", slug="imported-team-z")
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: object())
    monkeypatch.setattr(sync_team, "check_sync_preconditions", lambda *a, **k: {})
    monkeypatch.setattr(sync_team, "run_sync", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted despite --no-input"))

    Command().handle(**_force_delete_options(tmp_path, interactive=False))

    assert not Team.objects.filter(slug="imported-team-z").exists()  # deleted without prompting


def test_serialized_row_round_trips_through_importer(tmp_path, keypair):
    """The serializer's output is exactly what the importer consumes."""
    public_key, private_key = keypair
    provider = LlmProviderFactory(config={"api_key": "sk-live"})
    row = build_resource_serializer(LlmProvider)(provider, context={"public_key": public_key}).data

    store = FKTranslationStore(tmp_path / "t.sqlite")
    target_team = Team.objects.create(name="Target", slug="target-z")

    importer = Importer(store, private_key=private_key)
    importer.target_team = target_team  # normally captured when the team row is imported first
    importer.import_rows("service_providers.llmprovider", [dict(row)])

    imported = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", provider.id))
    assert imported.team_id == target_team.id  # assigned from the synced team, not carried in the row
    assert imported.config == {"api_key": "sk-live"}

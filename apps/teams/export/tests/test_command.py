import io

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management.base import CommandError

from apps.api.export.serializers import build_resource_serializer
from apps.service_providers.models import LlmProvider
from apps.teams.export import seal as seal_mod
from apps.teams.export.importer import Importer
from apps.teams.export.manifest import schema_checksum
from apps.teams.export.selection import SELECTION_CHANGED_DETAIL
from apps.teams.export.translation import ALL_CHATBOTS_KEY, selection_key
from apps.teams.management.commands import sync_team
from apps.teams.management.commands.sync_team import (
    PRIVATE_KEY_ENV_VAR,
    Command,
    _load_private_key,
    check_sync_preconditions,
    force_delete_team,
    run_sync,
)
from apps.teams.models import Team
from apps.users.models import CustomUser
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
        self.selections = []
        self.get_team_calls = 0

    def get_manifest(self):
        return self.manifest

    def get_team(self):
        # The team is fetched on its own from the dedicated ``team/`` endpoint, not the manifest loop.
        self.get_team_calls += 1
        return self.rows_by_resource["teams"][0]

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        self.iter_calls.append((resource, start_cursor))
        if selection not in self.selections:
            self.selections.append(selection)
        rows = list(self.rows_by_resource.get(resource, []))
        return iter([rows]) if rows else iter([])

    def get_file_content(self, file_id):
        return b""


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
                "is_migrating": True,
                "has_public_key": True,
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


def _private_pem(private):
    return private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _unseals(loaded_key, public_key):
    """The loaded private key can unseal what its matching public key sealed -- proves the right key
    was loaded, not just that some key came back."""
    return seal_mod.unseal(seal_mod.seal({"x": 1}, public_key), loaded_key) == {"x": 1}


def test_load_private_key_from_flag_file(tmp_path, keypair):
    public_key, private = keypair
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(_private_pem(private))

    assert _unseals(_load_private_key(str(key_file)), public_key)


def test_load_private_key_from_env_holds_key_contents(monkeypatch, keypair):
    """The env var carries the PEM key itself, not a path to it."""
    public_key, private = keypair
    monkeypatch.setenv(PRIVATE_KEY_ENV_VAR, _private_pem(private).decode())

    assert _unseals(_load_private_key(None), public_key)


def test_flag_file_takes_precedence_over_env(tmp_path, keypair, monkeypatch):
    public_key, private = keypair
    key_file = tmp_path / "key.pem"
    key_file.write_bytes(_private_pem(private))
    monkeypatch.setenv(PRIVATE_KEY_ENV_VAR, "not-a-real-key")  # would raise if the env were consulted

    assert _unseals(_load_private_key(str(key_file)), public_key)


def test_load_private_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv(PRIVATE_KEY_ENV_VAR, raising=False)
    assert _load_private_key(None) is None


def test_schema_checksum_mismatch_aborts(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + "-mismatch"
    store = make_store(tmp_path / "t.sqlite")
    with pytest.raises(CommandError, match="schema"):
        run_sync(FakeClient(manifest, rows), store, keypair[1])


def test_skip_schema_check_bypasses_mismatch(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] = manifest["schema_checksum"] + "-mismatch"
    store = make_store(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1], enforce_schema=False)

    assert Team.objects.filter(slug="imported-team-z").exists()


def test_aborts_when_secrets_present_but_no_private_key(make_store, tmp_path, keypair):
    """Without the private key the sealed secret fields would be imported as unreadable tokens, so
    the sync must refuse up front rather than silently corrupt provider configs / participant data."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    with pytest.raises(CommandError, match="private key"):
        run_sync(FakeClient(manifest, rows), store, None)

    assert not Team.objects.filter(slug="imported-team-z").exists()  # aborted before importing anything


def test_files_confirmation_is_asked_once_and_remembered(make_store, tmp_path, keypair, monkeypatch):
    """Declining the files prompt aborts without persisting anything; confirming is recorded in the
    state DB so a rerun (a fresh store over the same file) doesn't ask again."""
    manifest, rows = _scenario(keypair[0])
    client = FakeClient(manifest, rows)
    store = make_store(tmp_path / "t.sqlite")

    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")
    with pytest.raises(CommandError, match="files"):
        check_sync_preconditions(client, keypair[1], store=store)

    monkeypatch.setattr("builtins.input", lambda *a, **k: " Yes ")  # affirmative in any case/spacing
    check_sync_preconditions(client, keypair[1], store=store)

    reopened = make_store(tmp_path / "t.sqlite")
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted again after confirmation"))
    check_sync_preconditions(client, keypair[1], store=reopened)


def test_files_confirmation_is_not_remembered_when_preconditions_fail(make_store, tmp_path, keypair, monkeypatch):
    """A "yes" is only recorded once every other check passes, so an aborted run asks again."""
    manifest, rows = _scenario(keypair[0])
    manifest["schema_checksum"] += "-mismatch"
    store = make_store(tmp_path / "t.sqlite")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "yes")

    with pytest.raises(CommandError, match="schema"):
        check_sync_preconditions(FakeClient(manifest, rows), keypair[1], store=store)

    assert not store.has_flag(sync_team.FILES_CONFIRMED_FLAG)


def test_files_confirmation_fails_cleanly_without_a_terminal(make_store, tmp_path, keypair, monkeypatch):
    """EOF on stdin (cron, CI, piped input) must abort with a CommandError, not a raw EOFError."""

    def eof(*args, **kwargs):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    store = make_store(tmp_path / "t.sqlite")

    with pytest.raises(CommandError, match="interactively"):
        check_sync_preconditions(FakeClient(*_scenario(keypair[0])), keypair[1], store=store)


def _new_user_scenario():
    """A manifest and rows whose only entry is a single user the target does not have yet."""
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
                "is_migrating": True,
                "has_public_key": True,
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
    return entries, rows


def test_new_users_receive_a_password_reset_email(make_store, tmp_path, keypair):
    entries, rows = _new_user_scenario()
    store = make_store(tmp_path / "t.sqlite")
    mail.outbox.clear()

    run_sync(FakeClient(_manifest(entries), rows), store, keypair[1])

    assert any("added@example.com" in message.to for message in mail.outbox)


class _RejectingEmailBackend(BaseEmailBackend):
    """Stands in for SES in sandbox mode, which refuses any recipient it hasn't verified."""

    def send_messages(self, email_messages):
        raise RuntimeError("Email address is not verified")


def test_a_rejected_password_reset_email_is_reported_instead_of_aborting_the_sync(tmp_path, monkeypatch, settings):
    """A recipient the mail provider rejects must not take the sync down; the run finishes and the
    report names them so the reset can be sent by hand."""
    entries, rows = _new_user_scenario()
    settings.EMAIL_BACKEND = f"{__name__}._RejectingEmailBackend"
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: FakeClient(_manifest(entries), rows))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "yes")
    report = io.StringIO()

    Command(stdout=report).handle(**_sync_options(tmp_path))

    assert CustomUser.objects.filter(email="added@example.com").exists()
    assert "added@example.com" in report.getvalue()
    assert "Email address is not verified" in report.getvalue()


def test_run_sync_builds_team_and_resolves_secret_provider(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1])

    team = Team.objects.get(pk=store.get_target("teams.team", 9001))
    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == team.id
    assert provider.config == {"api_key": "sk-x"}
    assert store.has_unfilled_targets() is False


def test_rerun_is_a_no_op_and_resumes_from_stored_cursor(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    first = FakeClient(manifest, rows)
    run_sync(first, store, keypair[1])
    # once for the readiness precondition, once for the chatbot selection, once to fetch and import
    # the team (ResourceFetcher caches the response, so this is one request per run)
    assert first.get_team_calls == 3

    second = FakeClient(manifest, rows)
    run_sync(second, store, keypair[1])

    assert Team.objects.filter(slug="imported-team-z").count() == 1
    # the team is already synced, so the rerun reads the team only for the readiness precondition and
    # the selection, not to re-import it (that's loaded from the target DB)
    assert second.get_team_calls == 2
    # the second run resumes each pk resource from the cursor the first run stored
    assert dict(second.iter_calls)["llm_provider"] == "5"


def test_rerun_reanchors_scoped_rows_to_the_existing_team(make_store, tmp_path, keypair):
    """On rerun the team is loaded from the target DB (not re-fetched), and it still anchors the
    team-scoped rows -- the provider stays attached to the same imported team."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1])
    team = Team.objects.get(pk=store.get_target("teams.team", 9001))

    importer = run_sync(FakeClient(manifest, rows), store, keypair[1])

    assert importer.target_team == team
    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == team.id


def test_untracked_existing_team_aborts_and_suggests_force_delete(make_store, tmp_path, keypair):
    """A team already present locally but absent from the sync store must not be imported over: the
    sync aborts and points the operator at --force-delete, leaving the team and store untouched."""
    manifest, rows = _scenario(keypair[0])
    existing = Team.objects.create(name="Old name", slug="imported-team-z")
    store = make_store(tmp_path / "t.sqlite")

    with pytest.raises(CommandError, match="--force-delete"):
        run_sync(FakeClient(manifest, rows), store, keypair[1])

    existing.refresh_from_db()
    assert existing.name == "Old name"  # left untouched
    assert Team.objects.filter(slug="imported-team-z").count() == 1  # no duplicate created
    assert store.get_target("teams.team", 9001) is None  # not linked
    assert not LlmProvider.objects.filter(team=existing).exists()  # no scoped rows imported


def test_stale_sync_state_pointing_at_deleted_team_suggests_force_delete(make_store, tmp_path, keypair):
    """If the synced team was deleted locally but the sync state still references it, a rerun must
    abort with a CommandError pointing at --force-delete, not a raw Team.DoesNotExist traceback."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1])
    Team.objects.get(slug="imported-team-z").delete()

    with pytest.raises(CommandError, match="--force-delete"):
        run_sync(FakeClient(manifest, rows), store, keypair[1])


def test_first_time_import_creates_team(make_store, tmp_path, keypair):
    """A brand-new import (no local team with that slug) creates the team from the source."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1])

    assert Team.objects.filter(slug="imported-team-z").exists()


def test_first_time_import_enables_migration_mode_on_target(make_store, tmp_path, keypair):
    """The target team is frozen (migration mode on) the moment it's created, so this server doesn't
    start firing the team's events and scheduled messages while the still-live source is migrating.
    Migration mode isn't exported, so the sync sets it explicitly."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")

    run_sync(FakeClient(manifest, rows), store, keypair[1])

    assert Team.objects.get(slug="imported-team-z").is_migrating is True


def test_rerun_does_not_re_enable_migration_mode(make_store, tmp_path, keypair):
    """Migration mode is set only when the team is first created. If an operator turns it off (e.g. at
    cutover), a later rerun of the sync must not silently turn it back on."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1])

    team = Team.objects.get(slug="imported-team-z")
    team.is_migrating = False
    team.save(update_fields=["is_migrating"])

    run_sync(FakeClient(manifest, rows), store, keypair[1])

    team.refresh_from_db()
    assert team.is_migrating is False


class _FakeHTTPResponse:
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self._detail = detail

    def json(self):
        return {"detail": self._detail}


def _http_error(status_code, detail):
    return requests.HTTPError(response=_FakeHTTPResponse(status_code, detail))


class _RaisingClient(FakeClient):
    """A FakeClient whose ``get_team`` or ``iter_pages`` raises instead of returning data, to simulate
    an HTTP error surfacing from the source server mid-sync."""

    def __init__(self, manifest, rows, error, raise_on):
        super().__init__(manifest, rows)
        self._error = error
        self._raise_on = raise_on

    def get_team(self):
        if self._raise_on == "get_team":
            raise self._error
        return super().get_team()

    def iter_pages(self, resource, start_cursor=None, limit=100, selection=None):
        if self._raise_on == "iter_pages":
            raise self._error
        return super().iter_pages(resource, start_cursor, limit, selection)


@pytest.mark.parametrize(
    ("is_migrating", "has_public_key", "match"),
    [
        pytest.param(False, True, "Migration mode", id="not-migrating"),
        pytest.param(True, False, "no public key", id="no-public-key"),
        pytest.param(False, False, "Migration mode.*no public key", id="neither"),
    ],
)
def test_sync_blocks_when_source_team_is_not_ready(make_store, tmp_path, keypair, is_migrating, has_public_key, match):
    """Migration mode and a registered public key must both be set on the source team; the sync blocks
    up front (before any import) and names whatever is missing."""
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0].update(is_migrating=is_migrating, has_public_key=has_public_key)
    store = make_store(tmp_path / "t.sqlite")

    with pytest.raises(CommandError, match=match):
        run_sync(FakeClient(manifest, rows), store, keypair[1])

    assert not Team.objects.filter(slug="imported-team-z").exists()  # blocked before importing anything


def test_unrelated_403_surfaces_as_http_error(make_store, tmp_path, keypair):
    """A 403 from the source (e.g. a revoked API key) must surface as-is rather than be swallowed."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    error = _http_error(403, "You do not have permission to perform this action.")
    client = _RaisingClient(manifest, rows, error, raise_on="get_team")

    with pytest.raises(requests.HTTPError):
        run_sync(client, store, keypair[1])


def test_missing_public_key_400_raises_friendly_error(make_store, tmp_path, keypair):
    """The source returns a 400 when a secret resource is requested but its team has no public key to
    seal against; surface a friendly message instead of a raw HTTP traceback."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    error = _http_error(400, seal_mod.MISSING_PUBLIC_KEY_DETAIL)
    client = _RaisingClient(manifest, rows, error, raise_on="iter_pages")

    with pytest.raises(CommandError, match="no public key"):
        run_sync(client, store, keypair[1])


def test_unrelated_400_is_not_mistaken_for_missing_public_key(make_store, tmp_path, keypair):
    """A 400 for some other reason must surface as-is, not be reworded into a misleading
    'set the public key' message."""
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "t.sqlite")
    error = _http_error(400, "Invalid cursor.")
    client = _RaisingClient(manifest, rows, error, raise_on="iter_pages")

    with pytest.raises(requests.HTTPError):
        run_sync(client, store, keypair[1])


def test_force_delete_team_removes_team_and_resets_state(make_store, tmp_path):
    team = Team.objects.create(name="Old", slug="imported-team-z")
    state_db = tmp_path / "imported-team-z.sqlite"
    make_store(state_db).record("teams.team", 9001, team.id)
    assert state_db.exists()
    mail.outbox.clear()

    force_delete_team("imported-team-z", tmp_path)

    assert not Team.objects.filter(slug="imported-team-z").exists()
    assert not state_db.exists()
    assert mail.outbox == []  # a re-import must not notify anyone that their team was deleted


def test_force_delete_team_is_a_no_op_when_team_missing(tmp_path):
    force_delete_team("never-synced", tmp_path)  # no team, no state DB -- must not raise


def _sync_options(tmp_path, **overrides):
    options = {
        "source_url": "http://src",
        "api_key": "k",
        "team_slug": "imported-team-z",
        "private_key_path": None,
        "state_dir": str(tmp_path),
        "limit": 100,
        "skip_schema_check": False,
        "force_delete": False,
    }
    options.update(overrides)
    return options


def _force_delete_options(tmp_path, **overrides):
    return _sync_options(tmp_path, force_delete=True, **overrides)


def test_force_delete_aborts_when_confirmation_declined(tmp_path, monkeypatch):
    """An interactive --force-delete that isn't confirmed must abort before deleting anything."""
    Team.objects.create(name="Keep", slug="imported-team-z")
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: object())
    monkeypatch.setattr(sync_team, "check_sync_preconditions", lambda *a, **k: {})
    monkeypatch.setattr(sync_team, "check_force_delete_allowed", lambda _client: None)
    monkeypatch.setattr(sync_team, "run_sync", lambda *a, **k: pytest.fail("sync ran despite aborted delete"))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")

    with pytest.raises(CommandError, match="not confirmed"):
        Command().handle(**_force_delete_options(tmp_path))

    assert Team.objects.filter(slug="imported-team-z").exists()  # declined -> nothing deleted


def test_serialized_row_round_trips_through_importer(make_store, tmp_path, keypair):
    """The serializer's output is exactly what the importer consumes."""
    public_key, private_key = keypair
    provider = LlmProviderFactory(config={"api_key": "sk-live"})
    row = build_resource_serializer(LlmProvider)(provider, context={"public_key": public_key}).data

    store = make_store(tmp_path / "t.sqlite")
    target_team = Team.objects.create(name="Target", slug="target-z")

    importer = Importer(store, private_key=private_key)
    importer.target_team = target_team  # normally captured when the team row is imported first
    importer.import_rows("service_providers.llmprovider", [dict(row)])

    imported = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", provider.id))
    assert imported.team_id == target_team.id  # assigned from the synced team, not carried in the row
    assert imported.config == {"api_key": "sk-live"}


def test_run_sync_resumes_from_the_stored_cursor(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "service_providers.llmprovider", "42")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", "42") in client.iter_calls


def test_run_sync_records_a_cursor_after_each_page(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    store = make_store(tmp_path / "team.sqlite")

    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    assert store.get_cursor(ALL_CHATBOTS_KEY, "service_providers.llmprovider") == "5"


def _with_excluded_evaluators(manifest):
    manifest["entries"][0]["scope"] = "referenced"
    manifest["entries"].append(
        {
            "model": "evaluations.evaluator",
            "resource": "evaluators",
            "cursor": "pk",
            "secret": False,
            "scope": "excluded",
        }
    )
    return manifest


def test_excluded_resources_are_not_requested_under_a_selection(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    _with_excluded_evaluators(manifest)
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    store = make_store(tmp_path / "team.sqlite")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert "evaluators" not in {resource for resource, _cursor in client.iter_calls}


def test_excluded_resources_are_requested_without_a_selection(make_store, tmp_path, keypair):
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    _with_excluded_evaluators(manifest)
    store = make_store(tmp_path / "team.sqlite")

    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert "evaluators" in {resource for resource, _cursor in client.iter_calls}


def test_a_narrowed_selection_keeps_its_cursors(make_store, tmp_path, keypair):
    """The source then serves a subset of what it served, so every cursor is still valid."""
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"][0]["scope"] = "owned"
    store = make_store(tmp_path / "team.sqlite")

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}, {"public_id": "b", "name": "B"}]
    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", "5") in client.iter_calls


def test_a_selection_after_a_full_team_sync_keeps_its_cursors(make_store, tmp_path, keypair):
    """A full-team sync served a superset of any selection, so its cursors stay valid."""
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"][0]["scope"] = "owned"
    store = make_store(tmp_path / "team.sqlite")
    store.set_cursor(ALL_CHATBOTS_KEY, "service_providers.llmprovider", "5")

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", "5") in client.iter_calls


def test_a_widened_selection_starts_from_the_beginning(make_store, tmp_path, keypair):
    """Adding a chatbot puts rows below the cursor that were never synced, so resuming would skip them."""
    public_key, private = keypair
    manifest, rows = _scenario(public_key)
    manifest["entries"][0]["scope"] = "referenced"
    store = make_store(tmp_path / "team.sqlite")

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    run_sync(FakeClient(manifest, rows), store, private, on_user_created=None)

    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}, {"public_id": "b", "name": "B"}]
    client = FakeClient(manifest, rows)
    run_sync(client, store, private, on_user_created=None)

    assert ("llm_provider", None) in client.iter_calls


def test_referenced_resources_are_reread_from_the_start_under_a_selection(make_store, tmp_path, keypair):
    """A shared row the chatbot starts using later can have a lower pk than the stored cursor, so a
    referenced resource is re-read in full while a selection is active."""
    manifest, rows = _scenario(keypair[0])
    manifest["entries"][0]["scope"] = "referenced"
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    store = make_store(tmp_path / "team.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1], on_user_created=None)

    client = FakeClient(manifest, rows)
    run_sync(client, store, keypair[1], on_user_created=None)

    assert ("llm_provider", None) in client.iter_calls


def test_owned_resources_resume_from_their_cursor_under_a_selection(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    manifest["entries"][0]["scope"] = "owned"
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    store = make_store(tmp_path / "team.sqlite")
    run_sync(FakeClient(manifest, rows), store, keypair[1], on_user_created=None)

    client = FakeClient(manifest, rows)
    run_sync(client, store, keypair[1], on_user_created=None)

    assert ("llm_provider", "5") in client.iter_calls


def test_run_sync_pages_under_its_selection_key(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "a", "name": "A"}]
    client = FakeClient(manifest, rows)

    run_sync(client, make_store(tmp_path / "team.sqlite"), keypair[1], on_user_created=None)

    assert client.selections == [selection_key(["a"])]


def test_a_selection_change_during_the_run_stops_it_cleanly(make_store, tmp_path, keypair):
    manifest, rows = _scenario(keypair[0])
    client = _RaisingClient(manifest, rows, _http_error(409, SELECTION_CHANGED_DETAIL), raise_on="iter_pages")

    with pytest.raises(CommandError, match="selection changed"):
        run_sync(client, make_store(tmp_path / "team.sqlite"), keypair[1], on_user_created=None)


def test_report_names_the_synced_chatbots(capsys):
    command = Command()
    command._report(
        sync_complete=True,
        team_slug="acme",
        chatbots=[{"public_id": "abc", "name": "Support bot"}],
    )
    out = capsys.readouterr().out
    assert "Support bot" in out
    assert "evaluations" in out.lower()
    assert "human annotations" in out.lower()
    assert "transcript analyses" in out.lower()


def test_report_omits_the_chatbot_section_for_a_whole_team_sync(capsys):
    command = Command()
    command._report(sync_complete=True, team_slug="acme", chatbots=[])
    out = capsys.readouterr().out
    assert "Chatbots synced" not in out


def test_report_counts_rows_left_untouched(capsys):
    Command()._report(sync_complete=True, team_slug="acme", skipped_rows=7)
    assert "left untouched: 7" in capsys.readouterr().out


def test_force_delete_is_refused_while_the_source_has_a_selection(keypair):
    """--force-delete drops the whole local team, which would destroy chatbots synced under an
    earlier selection."""
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]

    with pytest.raises(CommandError, match="only part of the team"):
        sync_team.check_force_delete_allowed(FakeClient(manifest, rows))


def test_force_delete_is_allowed_for_a_whole_team_sync(keypair):
    manifest, rows = _scenario(keypair[0])
    sync_team.check_force_delete_allowed(FakeClient(manifest, rows))  # does not raise


def test_preflight_prints_what_the_source_will_export(keypair):
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    lines = []

    check_sync_preconditions(FakeClient(manifest, rows), keypair[1], write=lines.append)

    assert "The source will export 1 of this team's chatbots:" in lines
    assert "  - Support bot" in lines


def test_preflight_says_when_the_whole_team_is_exported(keypair):
    manifest, rows = _scenario(keypair[0])
    lines = []

    check_sync_preconditions(FakeClient(manifest, rows), keypair[1], write=lines.append)

    assert "The source will export the whole team." in lines


def test_force_delete_is_refused_before_the_prompt(tmp_path, monkeypatch, keypair):
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    Team.objects.create(name="Keep", slug="imported-team-z")
    monkeypatch.setattr(sync_team, "ResourceFetcher", lambda *a, **k: FakeClient(manifest, rows))
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("prompted despite the refusal"))

    with pytest.raises(CommandError, match="only part of the team"):
        Command().handle(**_force_delete_options(tmp_path))

    assert Team.objects.filter(slug="imported-team-z").exists()


def test_a_partial_sync_does_not_ask_about_the_files_bundle(make_store, tmp_path, keypair, monkeypatch):
    """create_team_files_zip_task only zips whole teams, so there is no bundle for the operator to
    have moved. The importer backfills each missing blob from the source instead."""
    manifest, rows = _scenario(keypair[0])
    rows["teams"][0]["exportable_chatbots"] = [{"public_id": "abc", "name": "Support bot"}]
    store = make_store(tmp_path / "team.sqlite")
    monkeypatch.setattr(sync_team, "_prompt", lambda _message: pytest.fail("prompted for the files bundle"))

    lines = []
    check_sync_preconditions(FakeClient(manifest, rows), keypair[1], store=store, write=lines.append)

    assert any("backfilled from the source" in line for line in lines)


def test_a_whole_team_sync_still_asks_about_the_files_bundle(make_store, tmp_path, keypair, monkeypatch):
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "team.sqlite")
    asked = []
    monkeypatch.setattr(sync_team, "_prompt", lambda message: asked.append(message) or "yes")

    check_sync_preconditions(FakeClient(manifest, rows), keypair[1], store=store)

    assert asked
    assert store.has_flag(sync_team.FILES_CONFIRMED_FLAG)


def test_a_whole_team_sync_aborts_when_the_files_were_not_moved(make_store, tmp_path, keypair, monkeypatch):
    manifest, rows = _scenario(keypair[0])
    store = make_store(tmp_path / "team.sqlite")
    monkeypatch.setattr(sync_team, "_prompt", lambda _message: "no")

    with pytest.raises(CommandError, match="storage backend"):
        check_sync_preconditions(FakeClient(manifest, rows), keypair[1], store=store)

    assert not store.has_flag(sync_team.FILES_CONFIRMED_FLAG)


def test_report_limits_the_webhook_command_to_the_synced_chatbots(capsys):
    Command()._report(sync_complete=True, team_slug="acme", chatbots=[{"public_id": "abc", "name": "Support bot"}])
    assert "reregister_webhooks --team-slug=acme --chatbot=abc" in capsys.readouterr().out

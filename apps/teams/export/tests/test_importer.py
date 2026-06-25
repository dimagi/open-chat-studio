from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group

from apps.chat.models import Chat
from apps.experiments.models import ConsentForm, ExperimentSession
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.export import seal as seal_mod
from apps.teams.export.importer import Importer, MissingGlobalRow
from apps.teams.export.translation import FKTranslationStore
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


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
        "groups": [],
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
    """A team row is created, its source->target id recorded, and source timestamps kept."""
    Importer(store).import_rows("teams.team", [_team_row()])

    target_pk = store.get_target("teams.team", 9001)
    team = Team.objects.get(pk=target_pk)
    assert team.slug == "imported-team-xyz"
    assert team.created_at == datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_import_resolves_fk_to_target_pk_and_unseals_secret(store, keypair):
    """A provider's team FK is rewritten to the target team and its sealed config decrypted."""
    public_key, private_key = keypair
    importer = Importer(store, private_key=private_key)
    importer.import_rows("teams.team", [_team_row()])
    target_team = store.get_target("teams.team", 9001)

    provider_row = {
        "id": 5,
        "name": "OpenAI",
        "type": "openai",
        "config": seal_mod.seal({"api_key": "sk-x"}, public_key),
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("service_providers.llmprovider", [provider_row])

    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == target_team  # assigned to the synced team, not read from the row
    assert provider.config == {"api_key": "sk-x"}


def test_rerun_does_not_duplicate(store):
    """Re-importing the same row maps to the existing target instead of creating a duplicate."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    first_pk = store.get_target("teams.team", 9001)
    importer.import_rows("teams.team", [_team_row()])
    assert store.get_target("teams.team", 9001) == first_pk
    assert Team.objects.filter(slug="imported-team-xyz").count() == 1


def test_global_row_matches_existing_and_is_not_recreated(store):
    """A global (teamless) row is matched to the shared target by natural key, not recreated."""
    existing = LlmProviderModel.objects.create(team=None, type="openai", name="gpt-glob", max_token_limit=8192)
    count_before = LlmProviderModel.objects.count()
    row = {
        "id": 77,
        "is_global": True,
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


def test_missing_global_row_raises_with_its_natural_key(store):
    """A global row the source serves but the target lacks must fail loudly (naming the missing row),
    not silently null the references that point at it or abort obscurely deep in FK resolution."""
    row = {
        "id": 77,
        "is_global": True,
        "type": "openai",
        "name": "model-not-on-target",
        "max_token_limit": 8192,
        "deprecated": False,
        "created_at": PAST,
        "updated_at": PAST,
    }

    with pytest.raises(MissingGlobalRow, match="model-not-on-target"):
        Importer(store).import_rows("service_providers.llmprovidermodel", [row])

    assert store.get_target("service_providers.llmprovidermodel", 77) is None


def test_node_params_and_fk_columns_are_remapped(store):
    """Resource ids in both FK columns and node params are translated to their target pks."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    importer.import_rows(
        "service_providers.llmprovider",
        [{"id": 7, "name": "P", "type": "openai", "config": {}, "created_at": PAST, "updated_at": PAST}],
    )
    provider_pk = store.get_target("service_providers.llmprovider", 7)
    importer.import_rows(
        "pipelines.pipeline",
        [
            {
                "id": 100,
                "name": "Flow",
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


def test_importing_user_creates_team_membership_with_role_groups(store):
    """A user row carries the user's role in the synced team; importing it creates the membership
    and links the role groups (matched by name) -- no separate membership resource needed."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    Group.objects.get_or_create(name="Sync Role X")

    importer.import_rows("users.customuser", [_user_row(50, "u@example.com", groups=["Sync Role X"])])

    user = CustomUser.objects.get(pk=store.get_target("users.customuser", 50))
    membership = Membership.objects.get(team_id=team_pk, user=user)
    assert list(membership.groups.values_list("name", flat=True)) == ["Sync Role X"]


def test_reimporting_user_does_not_duplicate_membership(store):
    """Re-running maps the user to the same membership rather than creating a second one."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    importer.import_rows("users.customuser", [_user_row(50, "u@example.com")])
    importer.import_rows("users.customuser", [_user_row(50, "u@example.com")])

    user = CustomUser.objects.get(pk=store.get_target("users.customuser", 50))
    assert Membership.objects.filter(team_id=team_pk, user=user).count() == 1


def _session_row(source_id=33, experiment_src=11, participant_src=22, chat=None):
    return {
        "id": source_id,
        "experiment": experiment_src,
        "participant": participant_src,
        "experiment_channel": None,
        "chat": chat if chat is not None else {"name": "Imported Chat", "translated_languages": ["en"], "metadata": {}},
        "created_at": PAST,
        "updated_at": PAST,
    }


def _seed_session_fks(store, team_pk):
    """Stand in a target experiment and participant for a session row to reference, recording their
    source->target translations so the session's FKs resolve."""
    team = Team.objects.get(pk=team_pk)
    experiment = ExperimentFactory(team=team)
    participant = ParticipantFactory(team=team)
    store.record("experiments.experiment", 11, experiment.id)
    store.record("experiments.participant", 22, participant.id)


def test_importing_session_creates_its_chat_inline(store):
    """A session's chat isn't its own synced resource -- importing the session creates the chat in
    the synced team from the fields embedded in the row."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    _seed_session_fks(store, team_pk)

    importer.import_rows(
        "experiments.experimentsession",
        [_session_row(chat={"name": "Imported Chat", "translated_languages": ["en"], "metadata": {"k": "v"}})],
    )

    session = ExperimentSession.objects.get(pk=store.get_target("experiments.experimentsession", 33))
    assert session.chat.name == "Imported Chat"
    assert session.chat.translated_languages == ["en"]
    assert session.chat.metadata == {"k": "v"}
    assert session.chat.team_id == team_pk


def test_reimporting_session_reuses_its_chat(store):
    """Re-running updates the session's existing chat rather than creating a duplicate."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    _seed_session_fks(store, team_pk)

    importer.import_rows("experiments.experimentsession", [_session_row()])
    session = ExperimentSession.objects.get(pk=store.get_target("experiments.experimentsession", 33))
    first_chat_pk = session.chat_id

    importer.import_rows("experiments.experimentsession", [_session_row(chat={"name": "Renamed", "metadata": {}})])

    session.refresh_from_db()
    assert session.chat_id == first_chat_pk  # same chat row, not a new one
    assert session.chat.name == "Renamed"
    assert Chat.objects.filter(pk=first_chat_pk).count() == 1


def test_default_consent_form_maps_to_auto_created_default(store):
    """The source's default consent form maps onto the team's auto-created default, not a second one."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    auto_default = ConsentForm.objects.get(team_id=team_pk, is_default=True)

    row = {
        "id": 300,
        "name": "Imported Consent",
        "consent_text": "Source consent text",
        "is_default": True,
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
    """A user already on the target is mapped by username, left unchanged, and gets no reset email --
    but is still given a membership in the synced team so they can access the imported data."""
    existing = CustomUser.objects.create(username="op@example.com", email="op@example.com", first_name="Original")
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    importer.import_rows("users.customuser", [_user_row(50, "op@example.com", first_name="Source")])

    assert store.get_target("users.customuser", 50) == existing.pk
    existing.refresh_from_db()
    assert existing.first_name == "Original"  # an existing account is mapped, never overwritten
    assert created_users == []  # not newly added -> no reset email
    assert Membership.objects.filter(team_id=team_pk, user=existing).exists()  # joined to the synced team


def test_new_user_triggers_on_user_created(store):
    """A newly created user fires the on_user_created callback (e.g. the reset email)."""
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("users.customuser", [_user_row(51, "fresh@example.com")])

    assert [u.username for u in created_users] == ["fresh@example.com"]


def _fail_when_filling_checkpoint(store):
    """Wrap store.record so it raises on the finalizing write (target_key set), simulating a crash
    after the row is inserted but before its checkpoint is filled."""
    real_record = store.record

    def record(content_type, source_key, target_key=None):
        if target_key is not None:
            raise RuntimeError("interrupted before checkpoint was filled")
        return real_record(content_type, source_key, target_key)

    return record


def test_interrupted_finalize_rolls_back_the_created_row(store, monkeypatch):
    """A crash after the INSERT but before the checkpoint is filled must roll the row back; an
    orphaned row with no checkpoint is exactly what a rerun would duplicate."""
    monkeypatch.setattr(store, "record", _fail_when_filling_checkpoint(store))

    with pytest.raises(RuntimeError):
        Importer(store).import_rows("teams.team", [_team_row()])

    assert Team.objects.filter(slug="imported-team-xyz").count() == 0


def test_rerun_after_interruption_creates_exactly_one_row(store, monkeypatch):
    """After an interrupted run rolls its row back, a clean rerun creates exactly one -- no duplicate."""
    monkeypatch.setattr(store, "record", _fail_when_filling_checkpoint(store))
    with pytest.raises(RuntimeError):
        Importer(store).import_rows("teams.team", [_team_row()])
    monkeypatch.undo()

    Importer(store).import_rows("teams.team", [_team_row()])

    assert Team.objects.filter(slug="imported-team-xyz").count() == 1

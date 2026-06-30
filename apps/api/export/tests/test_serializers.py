import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers
from rest_framework.renderers import JSONRenderer

from apps.api.export.serializers import (
    ManifestEntrySerializer,
    ManifestSerializer,
    build_resource_response_serializer,
    build_resource_serializer,
)
from apps.assessments.models import Score
from apps.chat.models import Chat
from apps.documents.models import CollectionFile
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData
from apps.files.models import File, FileChunkEmbedding
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.export import seal as seal_mod
from apps.teams.export.manifest import build_manifest, entry_model, get_manifest_entry, team_scoped_queryset
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.assessments import ScoreFactory
from apps.utils.factories.documents import CollectionFileFactory
from apps.utils.factories.experiment import (
    ChatFactory,
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private


def _serialize(model, instance, public_key=None):
    serializer = build_resource_serializer(model)
    return serializer(instance, context={"public_key": public_key}).data


def test_team_serializer_excludes_members_and_public_key_and_lists_flags():
    team = TeamFactory(public_key="should-not-appear")
    data = _serialize(Team, team)
    assert "members" not in data
    assert "public_key" not in data
    assert isinstance(data["feature_flags"], list)
    assert data["id"] == team.id
    assert "created_at" in data
    assert "updated_at" in data


def test_customuser_serializer_drops_password_and_perms():
    data = _serialize(CustomUser, UserFactory())
    assert "password" not in data
    assert "user_permissions" not in data
    assert "email" in data
    assert data["groups"] == []  # the team-role field; empty with no team in context


def test_user_serializer_includes_team_role_groups():
    """The user export carries the user's role within the exported team (their membership's groups),
    so the membership doesn't need its own resource. The user's own auth groups must not leak in."""
    team = TeamFactory()
    user = UserFactory()
    membership = Membership.objects.create(team=team, user=user)
    role, _ = Group.objects.get_or_create(name="Team Admin Role")
    membership.groups.add(role)
    auth_group, _ = Group.objects.get_or_create(name="Global Auth Group")
    user.groups.add(auth_group)

    serializer = build_resource_serializer(CustomUser)
    data = serializer(user, context={"public_key": None, "team": team}).data

    assert data["groups"] == ["Team Admin Role"]  # the team role, not the user's global auth groups


def test_user_role_groups_via_export_queryset_is_team_scoped():
    """Through the export queryset the membership prefetch is filtered to the exported team, so a user
    in several teams exports only the context team's role groups -- not the others'."""
    team_a = TeamFactory()
    team_b = TeamFactory()
    user = UserFactory()
    Membership.objects.create(team=team_a, user=user).groups.add(Group.objects.get_or_create(name="Role A")[0])
    Membership.objects.create(team=team_b, user=user).groups.add(Group.objects.get_or_create(name="Role B")[0])

    qs = team_scoped_queryset(get_manifest_entry("users"), team_a)
    rows = build_resource_serializer(CustomUser)(qs, many=True, context={"team": team_a, "public_key": None}).data
    row = next(r for r in rows if r["id"] == user.id)

    assert row["groups"] == ["Role A"]


def test_user_role_groups_prefetch_avoids_n_plus_one():
    """The role-group field reads a prefetch, so total query count is constant regardless of how many
    users the page holds (no per-row membership/group query)."""
    serializer = build_resource_serializer(CustomUser)

    def export_query_count(team):
        qs = team_scoped_queryset(get_manifest_entry("users"), team)
        with CaptureQueriesContext(connection) as ctx:
            _ = serializer(qs, many=True, context={"team": team, "public_key": None}).data
        return len(ctx.captured_queries)

    role = Group.objects.get_or_create(name="Role")[0]
    few = TeamFactory()
    many = TeamFactory()
    for team, count in [(few, 2), (many, 12)]:
        for _ in range(count):
            Membership.objects.create(team=team, user=UserFactory()).groups.add(role)

    assert export_query_count(few) == export_query_count(many)


def test_llmprovider_config_is_sealed_and_round_trips(keypair):
    public_key, private_key = keypair
    provider = LlmProviderFactory(config={"openai_api_key": "sk-secret"})
    data = _serialize(LlmProvider, provider, public_key)

    assert data["config"] != {"openai_api_key": "sk-secret"}
    assert "sk-secret" not in str(data["config"])
    assert seal_mod.unseal(data["config"], private_key) == {"openai_api_key": "sk-secret"}
    assert "team" not in data  # the per-row team FK is dropped; the importer assigns the synced team


def test_participantdata_seals_data_and_encryption_key(keypair):
    public_key, private_key = keypair
    experiment = ExperimentFactory()
    participant = ParticipantFactory(team=experiment.team)
    pd = ParticipantData.objects.create(
        team=experiment.team,
        experiment=experiment,
        participant=participant,
        data={"name": "Jo"},
        encryption_key="abc123",
    )
    data = _serialize(ParticipantData, pd, public_key)
    assert seal_mod.unseal(data["data"], private_key) == {"name": "Jo"}
    assert seal_mod.unseal(data["encryption_key"], private_key) == "abc123"


def test_experiment_fk_serializes_as_source_pk():
    consent = ConsentFormFactory()
    experiment = ExperimentFactory(team=consent.team, consent_form=consent)
    data = _serialize(Experiment, experiment)
    assert data["consent_form"] == consent.id


def test_session_serializer_references_chat_by_fk():
    """A session's chat is its own synced resource, so the session row carries the chat as a plain
    FK pk rather than embedding its fields."""
    session = ExperimentSessionFactory()
    data = _serialize(ExperimentSession, session)
    assert data["chat"] == session.chat_id


def test_chat_serializer_dumps_its_fields_and_drops_team():
    """Chat is a standalone resource: it dumps its own fields and, like every team-scoped row, omits
    the redundant team FK."""
    chat = ChatFactory(translated_languages=["en", "fr"], metadata={"k": "v"})
    data = _serialize(Chat, chat)
    assert data["translated_languages"] == ["en", "fr"]
    assert data["metadata"] == {"k": "v"}
    assert "team" not in data


@pytest.mark.parametrize(
    "is_global",
    [
        pytest.param(True, id="global"),
        pytest.param(False, id="team_scoped"),
    ],
)
def test_global_able_model_exposes_is_global_flag_instead_of_team(is_global):
    team = None if is_global else TeamFactory()
    model = LlmProviderModel.objects.create(team=team, type="openai", name="gpt-x", max_token_limit=8192)
    data = _serialize(LlmProviderModel, model)
    assert data["is_global"] is is_global
    assert "team" not in data


def test_generic_fk_content_type_serializes_as_model_label():
    """A generic FK's content type is emitted as its ``app_label.model`` label, not the source
    ContentType pk (which is server-specific). The object id stays the source target-row pk; the
    importer resolves both on the target."""
    score = ScoreFactory()  # target is an ExperimentSession
    data = _serialize(Score, score)
    assert data["target_content_type"] == "experiments.experimentsession"
    assert data["target_object_id"] == score.target_object_id


def test_response_envelope_has_cursor_has_more_and_results():
    fields = build_resource_response_serializer(get_manifest_entry("users"))().fields
    assert set(fields) == {"cursor", "has_more", "results"}
    assert isinstance(fields["results"], serializers.ListSerializer)


def test_secret_fields_documented_as_sealed_strings():
    # llm_providers' `config` is sealed to a base64 string at runtime, not its raw model type.
    model = entry_model(get_manifest_entry("llm_providers").model)
    fields = build_resource_serializer(model)().fields
    assert isinstance(fields["config"], serializers.CharField)


def test_manifest_serializer_matches_build_manifest_payload():
    """The documented manifest response must stay in step with what the view actually returns."""
    manifest = build_manifest()
    assert set(ManifestSerializer().fields) == set(manifest)
    declared = set(ManifestEntrySerializer().fields)
    actual_keys = set().union(*(entry.keys() for entry in manifest["entries"]))
    assert declared == actual_keys


def test_file_field_serializes_as_relative_name_not_media_url():
    """A FileField serializes to its stored relative name so the importer can assign it straight
    back; the default ``/media/...`` URL is an absolute path Django's storage rejects on save."""
    file = FileFactory()
    data = build_resource_serializer(File)(file, context={"public_key": None}).data
    assert data["file"] == file.file.name
    assert not data["file"].startswith("/")


def test_halfvector_field_serializes_as_list_of_floats():
    """A pgvector column reads back as a non-iterable HalfVector object; the serializer must still
    emit a plain list of floats the importer can assign straight back."""
    size = settings.EMBEDDING_VECTOR_SIZE
    created = FileChunkEmbeddingFactory(embedding=[0.5] * size)
    instance = FileChunkEmbedding.objects.get(pk=created.pk)  # re-fetch so embedding is the DB object
    data = build_resource_serializer(FileChunkEmbedding)(instance, context={"public_key": None}).data
    assert data["embedding"] == [0.5] * size


def test_pydantic_schema_field_serializes_as_json_object():
    """A SchemaField (pydantic) serializes to a JSON object. The default JSONField mapping leaves the
    pydantic instance in place, which DRF's JSON encoder mangles into (field, value) pairs the
    importer can't read back. The corruption is at encode time, so render to JSON to catch it."""
    metadata = {"chunking_strategy": {"chunk_size": 400, "chunk_overlap": 200}}
    cf = CollectionFileFactory(metadata=metadata)
    instance = CollectionFile.objects.get(pk=cf.pk)
    data = build_resource_serializer(CollectionFile)(instance, context={"public_key": None}).data
    rendered = json.loads(JSONRenderer().render(data))
    assert rendered["metadata"] == metadata

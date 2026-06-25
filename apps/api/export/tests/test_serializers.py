import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group

from apps.api.export.serializers import build_resource_serializer
from apps.chat.models import Chat
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.export import seal as seal_mod
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.experiment import (
    ChatFactory,
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
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


@pytest.mark.parametrize("is_global", [True, False])
def test_global_able_model_exposes_is_global_flag_instead_of_team(is_global):
    team = None if is_global else TeamFactory()
    model = LlmProviderModel.objects.create(team=team, type="openai", name="gpt-x", max_token_limit=8192)
    data = _serialize(LlmProviderModel, model)
    assert data["is_global"] is is_global
    assert "team" not in data

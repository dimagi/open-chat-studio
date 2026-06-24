import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group

from apps.api.general.serializers import build_resource_serializer
from apps.experiments.models import Experiment, ParticipantData
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.teams.export import seal as seal_mod
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
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
    assert "groups" not in data
    assert "user_permissions" not in data
    assert "email" in data


def test_membership_groups_serialize_as_names():
    membership = MembershipFactory()
    group, _ = Group.objects.get_or_create(name="Sync Test Role")
    membership.groups.add(group)
    data = _serialize(Membership, membership)
    assert data["groups"] == ["Sync Test Role"]


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


@pytest.mark.parametrize("is_global", [True, False])
def test_global_able_model_exposes_is_global_flag_instead_of_team(is_global):
    team = None if is_global else TeamFactory()
    model = LlmProviderModel.objects.create(team=team, type="openai", name="gpt-x", max_token_limit=8192)
    data = _serialize(LlmProviderModel, model)
    assert data["is_global"] is is_global
    assert "team" not in data

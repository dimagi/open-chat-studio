"""PATCH /api/v2/chatbots/{id}/ -- settings and wiring by id (#4139, spec 5.1).

Key paths mirror GET /chatbots/{id}/inspect/, so a read-modify-write loop needs no remapping
except for references, which are addressed by id using the same ``<resource>_id`` convention the
discovery endpoints use.
"""

import unicodedata

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.experiments.models import Experiment
from apps.service_providers.models import VoiceProviderType
from apps.utils.factories.experiment import ChatbotFactory, ConsentFormFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory, VoiceProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def chatbot(db):
    """`ChatbotFactory` gives an Experiment with a Start -> End pipeline and no consent form or
    voice set, which is the state a freshly created chatbot is in."""
    return ChatbotFactory.create(team=TeamWithUsersFactory.create(), name="Support bot", description="")


@pytest.fixture()
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)


def _url(chatbot):
    return f"/api/v2/chatbots/{chatbot.public_id}/"


@pytest.mark.django_db()
def test_patch_updates_top_level_fields(client, chatbot):
    response = client.patch(_url(chatbot), {"name": "Renamed", "description": "New"}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.name, chatbot.description) == ("Renamed", "New")
    assert response.json()["name"] == "Renamed"


@pytest.mark.django_db(transaction=False)
def test_patch_locks_the_chatbot_row(client, chatbot):
    """Model.save() writes every column, so without the lock two concurrent PATCHes naming
    different fields would clobber one another. Query capture rather than threads: a real
    concurrency test would be slow and flaky."""
    with CaptureQueriesContext(connection) as captured:
        assert client.patch(_url(chatbot), {"name": "Locked"}, format="json").status_code == 200

    assert any("FOR UPDATE" in query["sql"] for query in captured.captured_queries)


@pytest.mark.django_db()
def test_patch_normalizes_the_name_to_nfc(client, chatbot):
    """Built with `unicodedata` for the same reason as the create-side twin: written as literals,
    the decomposed and composed forms look identical in source and the test can silently rot."""
    decomposed = unicodedata.normalize("NFD", "Café bot")
    composed = unicodedata.normalize("NFC", "Café bot")
    assert decomposed != composed  # guards the guard

    client.patch(_url(chatbot), {"name": decomposed}, format="json")

    chatbot.refresh_from_db()
    assert chatbot.name == composed


@pytest.mark.django_db()
def test_settings_merge_rather_than_replace(client, chatbot):
    """A partial PATCH of one settings key must not reset the other six to their defaults."""
    chatbot.echo_transcript = False
    chatbot.file_uploads_enabled = True
    chatbot.participant_allowlist = ["alice"]
    chatbot.save()

    response = client.patch(_url(chatbot), {"settings": {"seed_message": "Hi!"}}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.seed_message == "Hi!"
    assert chatbot.echo_transcript is False
    assert chatbot.file_uploads_enabled is True
    assert chatbot.participant_allowlist == ["alice"]


@pytest.mark.django_db()
def test_response_settings_path_matches_the_request_path(client, chatbot):
    """The mirror that makes read-modify-write cheap: the key you write is the key you read back."""
    body = client.patch(_url(chatbot), {"settings": {"seed_message": "Hi!"}}, format="json").json()

    assert body["settings"]["seed_message"] == "Hi!"


@pytest.mark.django_db()
def test_patch_sets_a_consent_form_by_id(client, chatbot):
    consent_form = ConsentFormFactory.create(team=chatbot.team)

    response = client.patch(_url(chatbot), {"consent_form_id": consent_form.id}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.consent_form == consent_form
    assert response.json()["consent_form_id"] == consent_form.id


@pytest.mark.django_db()
def test_patch_clears_a_consent_form_with_null(client, chatbot):
    chatbot.consent_form = ConsentFormFactory.create(team=chatbot.team)
    chatbot.save()

    client.patch(_url(chatbot), {"consent_form_id": None}, format="json")

    chatbot.refresh_from_db()
    assert chatbot.consent_form is None


@pytest.mark.django_db()
def test_patch_sets_a_trace_provider_by_id(client, chatbot):
    provider = TraceProviderFactory.create(team=chatbot.team)

    client.patch(_url(chatbot), {"trace_provider_id": provider.id}, format="json")

    chatbot.refresh_from_db()
    assert chatbot.trace_provider == provider


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field", "make_other_teams_row"),
    [
        pytest.param(
            "consent_form_id",
            lambda: ConsentFormFactory.create(team=TeamWithUsersFactory.create()).id,
            id="consent-form",
        ),
        pytest.param(
            "trace_provider_id",
            lambda: TraceProviderFactory.create(team=TeamWithUsersFactory.create()).id,
            id="trace-provider",
        ),
    ],
)
def test_another_teams_reference_is_a_400_on_that_field(client, chatbot, field, make_other_teams_row):
    """Team scoping makes a cross-team id indistinguishable from a nonexistent one, which is the
    point: neither is usable, and telling them apart would leak existence across teams."""
    response = client.patch(_url(chatbot), {field: make_other_teams_row()}, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db()
@pytest.mark.parametrize("field", ["consent_form_id", "trace_provider_id"], ids=["consent-form", "trace-provider"])
def test_a_nonexistent_reference_is_a_400_on_that_field(client, chatbot, field):
    response = client.patch(_url(chatbot), {field: 99999}, format="json")

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.django_db()
def test_another_teams_chatbot_is_a_404(chatbot):
    other = TeamWithUsersFactory.create()
    client = ApiTestClient(other.members.first(), other)

    assert client.patch(_url(chatbot), {"name": "Nope"}, format="json").status_code == 404


@pytest.mark.django_db()
def test_a_version_snapshot_is_a_404(client, chatbot):
    """Snapshots are immutable; writes only ever target the working version."""
    snapshot = Experiment.objects.create(
        team=chatbot.team, owner=chatbot.owner, name=chatbot.name, working_version=chatbot, version_number=1
    )

    assert client.patch(_url(snapshot), {"name": "Nope"}, format="json").status_code == 404


@pytest.mark.django_db()
def test_put_is_not_routed(client, chatbot):
    """PATCH only: a whole-chatbot replace is not part of the spec."""
    assert client.put(_url(chatbot), {"name": "Nope"}, format="json").status_code == 405


@pytest.mark.django_db()
def test_read_only_key_cannot_patch(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, read_only=True)

    assert client.patch(_url(chatbot), {"name": "Nope"}, format="json").status_code == 403


@pytest.mark.django_db()
def test_oauth_token_without_the_write_scope_cannot_patch(chatbot):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team, auth_method="oauth", scopes=["chatbots:read"])

    assert client.patch(_url(chatbot), {"name": "Nope"}, format="json").status_code == 403


@pytest.mark.django_db()
def test_patch_sets_a_general_voice(client, chatbot):
    """A general voice (voice_provider_id: null) pairs with any provider of the matching type."""
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)

    response = client.patch(
        _url(chatbot),
        {"voice": {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}},
        format="json",
    )

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (provider, voice)
    assert response.json()["voice"] == {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}


@pytest.mark.django_db()
def test_patch_sets_a_provider_owned_voice(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=provider)

    response = client.patch(
        _url(chatbot),
        {"voice": {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}},
        format="json",
    )

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.synthetic_voice == voice


@pytest.mark.django_db()
def test_patch_rejects_a_voice_of_the_wrong_type(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="Azure", voice_provider=None)

    response = client.patch(
        _url(chatbot),
        {"voice": {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}},
        format="json",
    )

    assert response.status_code == 400
    assert "voice" in response.json()


@pytest.mark.django_db()
def test_patch_rejects_a_voice_owned_by_another_provider(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    other_provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=other_provider)

    response = client.patch(
        _url(chatbot),
        {"voice": {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db()
def test_patch_rejects_another_teams_voice_provider(client, chatbot):
    other_team = TeamWithUsersFactory.create()
    provider = VoiceProviderFactory.create(team=other_team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)

    response = client.patch(
        _url(chatbot),
        {"voice": {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db()
@pytest.mark.parametrize("supplied", ["voice_provider_id", "synthetic_voice_id"])
def test_patch_rejects_half_a_voice_pair(client, chatbot, supplied):
    """The root PATCH is partial, and partial propagates into nested serializers -- without an
    explicit presence check, half a pair would slip through."""
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)
    values = {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id}

    response = client.patch(_url(chatbot), {"voice": {supplied: values[supplied]}}, format="json")

    assert response.status_code == 400


@pytest.mark.django_db()
def test_null_voice_clears_both_columns(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    chatbot.voice_provider = provider
    chatbot.synthetic_voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)
    chatbot.save()

    response = client.patch(_url(chatbot), {"voice": None}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.voice_provider is None
    assert chatbot.synthetic_voice is None
    assert response.json()["voice"] is None


@pytest.mark.django_db()
def test_omitting_voice_leaves_it_untouched(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    chatbot.voice_provider = provider
    chatbot.synthetic_voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)
    chatbot.save()

    client.patch(_url(chatbot), {"name": "Renamed"}, format="json")

    chatbot.refresh_from_db()
    assert chatbot.voice_provider == provider

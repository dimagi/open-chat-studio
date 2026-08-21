"""PATCH /api/v2/chatbots/{id}/ -- settings and wiring by id (#4139, spec 5.1).

The writable surface is the same field set the UI's ChatbotSettingsForm exposes, with references
as ``<field>_id`` keys. The ``settings`` block's nesting matches the inspect response. Unrecognised
keys are rejected with 400.
"""

import unicodedata

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.experiments.models import Experiment
from apps.service_providers.models import VoiceProviderType
from apps.teams.flags import Flags
from apps.teams.models import Flag
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


@pytest.mark.django_db()
def test_the_response_can_be_sent_straight_back_as_a_request(client, chatbot):
    """The consumer here is an agent, for which read-modify-write is the ordinary way to edit, so
    everything this endpoint returns other than the read-only identity keys has to be accepted
    back. `description` is the one that breaks it: the column is `TextField(null=True, default="")`
    with no `blank=True`, so ModelSerializer generates `allow_blank=False` and the endpoint refuses
    its own response for every chatbot POST /chatbots/ created."""
    first = client.patch(_url(chatbot), {"name": "Support bot"}, format="json")
    assert first.status_code == 200
    assert first.json()["description"] == ""

    read_only = {"id", "pipeline_id", "version_number"}
    echoed = {key: value for key, value in first.json().items() if key not in read_only}

    assert client.patch(_url(chatbot), echoed, format="json").status_code == 200, echoed


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        pytest.param("Rewritten", "Rewritten", id="text-is-kept"),
        pytest.param("", "", id="blank-clears-it"),
        pytest.param(None, "", id="null-is-normalised-to-blank"),
    ],
)
def test_description_accepts_blank_and_null(client, chatbot, sent, stored):
    """Null is accepted because rows predating this endpoint may hold SQL NULL and their own
    response has to be echoable; it is stored as "" so only one representation is ever written."""
    chatbot.description = "Original"
    chatbot.save()

    response = client.patch(_url(chatbot), {"description": sent}, format="json")

    assert response.status_code == 200, response.content
    chatbot.refresh_from_db()
    assert chatbot.description == stored


@pytest.mark.django_db()
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
def test_a_name_that_nfc_lengthens_past_the_column_is_a_400(client, chatbot):
    """NFC can make a string *longer*, so normalising after `max_length` ran would clear the check
    and then overflow `varchar(128)` on save. Create-side twin in test_chatbot_create.py."""
    over_long = "क़" * 128
    assert len(unicodedata.normalize("NFC", over_long)) > 128  # guards the guard

    assert client.patch(_url(chatbot), {"name": over_long}, format="json").status_code == 400


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
def test_the_allowlist_is_normalized_the_way_the_form_normalizes_it(client, chatbot):
    """`is_participant_allowed` matches identifiers exactly, and the UI form strips spaces on the
    way in. Stored as written, a human-formatted phone number -- which is what an LLM produces --
    gives an allowlist that looks configured and admits nobody, with no error to say so."""
    response = client.patch(_url(chatbot), {"settings": {"participant_allowlist": ["+27 82 000 0000"]}}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.participant_allowlist == ["+27820000000"]
    assert chatbot.is_participant_allowed("+27820000000")


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
@pytest.mark.parametrize(
    ("body", "under", "key"),
    [
        pytest.param({"nmae": "Typo"}, None, "nmae", id="at-the-root"),
        pytest.param({"settings": {"seed_mesage": "Hi!"}}, "settings", "seed_mesage", id="under-settings"),
    ],
)
def test_an_unrecognised_key_is_a_400_naming_it(client, chatbot, body, under, key):
    """DRF drops undeclared keys silently, so a typo is a 200 that wrote nothing. A human notices
    that from the echoed body; an agent reads the 200 and moves on.

    ``settings`` gets the check too, which it cannot take from ``initial_data`` -- that is set on the
    root serializer alone.
    """
    response = client.patch(_url(chatbot), body, format="json")

    assert response.status_code == 400
    errors = response.json()
    assert key in (errors[under] if under else errors)
    chatbot.refresh_from_db()
    assert chatbot.name == "Support bot"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("owner", None, id="owner"),
        pytest.param("team", None, id="team"),
        pytest.param("is_archived", True, id="is-archived"),
    ],
)
def test_a_protected_model_field_stays_unwritable(client, chatbot, field, value):
    """These columns were already refused by being undeclared. Rejecting unknown keys must keep
    them refused rather than promote them to an accepted spelling."""
    before = getattr(chatbot, field)

    response = client.patch(_url(chatbot), {field: value}, format="json")

    assert response.status_code == 400
    assert field in response.json()
    chatbot.refresh_from_db()
    assert getattr(chatbot, field) == before


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
def test_a_malformed_chatbot_id_is_a_404(client):
    """The router's lookup regex admits any non-slash string, so a non-UUID reaches the queryset
    and `UUIDField.to_python` raises. Unhandled that is a 500 -- a server fault reported for what
    is only a bad id."""
    assert client.patch("/api/v2/chatbots/not-a-uuid/", {"name": "Nope"}, format="json").status_code == 404


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
def test_machine_token_can_patch(chatbot):
    """A client-credentials token has no user, so every user-derived permission gate defers to the
    OAuth scope. That path is what a headless agent uses, and only create was covering it.

    The application has to be pinned to this chatbot: the scope alone is not enough, since the
    allowlist is the finer boundary for a credential with no user behind it. The refusal case lives
    in `test_auth.py` alongside the rest of the authorization gates.
    """
    client = ApiTestClient(
        chatbot.team.members.first(),
        chatbot.team,
        auth_method="oauth_client_credentials",
        scopes=["chatbots:write"],
        allowed_chatbots=[chatbot],
    )

    response = client.patch(_url(chatbot), {"name": "Headless edit"}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert chatbot.name == "Headless edit"


def _aws_pair(chatbot, *, owned_by_provider=False):
    """A voice provider on the chatbot's team and an AWS voice it can speak."""
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=provider if owned_by_provider else None)
    return provider, voice


def _store_voice(chatbot, provider, voice):
    chatbot.voice_provider = provider
    chatbot.synthetic_voice = voice
    chatbot.save()


@pytest.mark.django_db()
def test_patch_sets_a_general_voice(client, chatbot):
    """A general voice -- one with no owning provider -- pairs with any provider of its type."""
    provider, voice = _aws_pair(chatbot)

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (provider, voice)
    body = response.json()
    assert (body["voice_provider_id"], body["synthetic_voice_id"]) == (provider.id, voice.id)


@pytest.mark.django_db()
def test_patch_sets_a_provider_owned_voice(client, chatbot):
    provider, voice = _aws_pair(chatbot, owned_by_provider=True)

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (provider, voice)


@pytest.mark.django_db()
def test_patch_rejects_a_voice_of_the_wrong_type(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="Azure", voice_provider=None)

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == 400
    assert set(response.json()) == {"voice_provider_id", "synthetic_voice_id"}


@pytest.mark.django_db()
def test_patch_rejects_a_voice_owned_by_another_provider(client, chatbot):
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    other_provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=other_provider)

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == 400
    assert set(response.json()) == {"voice_provider_id", "synthetic_voice_id"}


@pytest.mark.django_db()
def test_patch_rejects_another_teams_voice_provider(client, chatbot):
    other_team = TeamWithUsersFactory.create()
    provider = VoiceProviderFactory.create(team=other_team, type=VoiceProviderType.aws)
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == 400
    assert "voice_provider_id" in response.json()


@pytest.mark.django_db()
def test_patch_validates_one_half_against_the_stored_other(client, chatbot):
    """The two fields are written independently under a partial PATCH, so a lone half is checked
    against the half already on the row rather than waved through."""
    provider, voice = _aws_pair(chatbot)
    _store_voice(chatbot, provider, voice)
    replacement = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)

    response = client.patch(_url(chatbot), {"synthetic_voice_id": replacement.id}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (provider, replacement)


@pytest.mark.django_db()
def test_patch_rejects_one_half_that_clashes_with_the_stored_other(client, chatbot):
    provider, voice = _aws_pair(chatbot)
    _store_voice(chatbot, provider, voice)
    azure_voice = SyntheticVoiceFactory.create(service="Azure", voice_provider=None)

    response = client.patch(_url(chatbot), {"synthetic_voice_id": azure_voice.id}, format="json")

    assert response.status_code == 400
    assert "synthetic_voice_id" in response.json()
    chatbot.refresh_from_db()
    assert chatbot.synthetic_voice == voice


@pytest.mark.django_db()
def test_patch_clears_both_voice_columns(client, chatbot):
    provider, voice = _aws_pair(chatbot)
    _store_voice(chatbot, provider, voice)

    response = client.patch(_url(chatbot), {"voice_provider_id": None, "synthetic_voice_id": None}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (None, None)
    body = response.json()
    assert (body["voice_provider_id"], body["synthetic_voice_id"]) == (None, None)


@pytest.mark.django_db()
def test_patch_rejects_clearing_only_the_provider(client, chatbot):
    """A voice with no provider cannot be spoken, so half a pair is not a state you can PATCH into."""
    provider, voice = _aws_pair(chatbot)
    _store_voice(chatbot, provider, voice)

    response = client.patch(_url(chatbot), {"voice_provider_id": None}, format="json")

    assert response.status_code == 400
    assert "voice_provider_id" in response.json()
    chatbot.refresh_from_db()
    assert chatbot.voice_provider == provider


@pytest.mark.django_db()
def test_patch_rejects_a_lone_half_when_nothing_is_stored(client, chatbot):
    """`chatbot` has no voice, so there is no stored provider for this voice to be checked against."""
    voice = SyntheticVoiceFactory.create(service="AWS", voice_provider=None)

    response = client.patch(_url(chatbot), {"synthetic_voice_id": voice.id}, format="json")

    assert response.status_code == 400
    assert "synthetic_voice_id" in response.json()


@pytest.mark.django_db()
def test_a_patch_that_leaves_the_voice_alone_does_not_validate_it(client, chatbot):
    """Nothing at the database level pairs these two columns, so a row can already hold half a
    pair. Re-checking an untouched voice would fail a PATCH that has nothing to do with it."""
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.aws)
    _store_voice(chatbot, provider, None)

    response = client.patch(_url(chatbot), {"name": "Renamed"}, format="json")

    assert response.status_code == 200
    chatbot.refresh_from_db()
    assert (chatbot.name, chatbot.voice_provider) == ("Renamed", provider)


@pytest.mark.django_db()
@pytest.mark.parametrize("key", ["provider_name", "voice_name", "type", "language", "neural"])
def test_only_the_voice_ids_are_writable(client, chatbot, key):
    """Inspect's voice object describes the voice as well as addressing it. Those descriptive keys
    are not part of the write surface, so they are refused rather than quietly dropped."""
    response = client.patch(_url(chatbot), {key: "whatever"}, format="json")

    assert response.status_code == 400
    assert key in response.json()


def _activate_for_the_team(flag, team):
    flag.teams.add(team)


def _activate_for_everyone(flag, team):
    flag.everyone = True
    flag.save()


@pytest.fixture()
def openai_voice_engine_flag(db):
    """The flag row, with waffle's cache flushed either side.

    Waffle caches a flag outside the test transaction, so without this one parametrised case's
    activation survives the rollback and the next case reads it -- the "off" case passes or fails
    on test order alone. An existing-but-unactivated row is equivalent to no row here: both leave
    ``is_active`` false.
    """
    flag, _ = Flag.objects.get_or_create(name=Flags.OPEN_AI_VOICE_ENGINE.slug)
    flag.flush()
    yield flag
    flag.flush()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("activate", "expected_status"),
    [
        pytest.param(_activate_for_the_team, 200, id="on-for-the-team-accepts-the-voice"),
        pytest.param(_activate_for_everyone, 200, id="on-for-everyone-accepts-it-too"),
        pytest.param(None, 400, id="off-refuses-it"),
    ],
)
def test_the_voice_fields_honour_the_openai_voice_engine_flag(
    client, chatbot, openai_voice_engine_flag, activate, expected_status
):
    """`ChatbotSettingsForm` offers neither OpenAI Voice Engine voices nor their providers unless the
    flag is on. Accepting one here would wire the chatbot to a voice the settings form's own
    querysets exclude, and saving that page would then fail on a field the user never touched.

    The `everyone` case is the one that matters for parity: the form asks waffle's full predicate --
    `everyone`, `percent`, the user and group lists -- *or* our team override, so checking only the
    team half would leave the settings page offering voices this endpoint rejects the moment the
    flag is switched on any other way, which is exactly what a normal rollout does."""
    provider = VoiceProviderFactory.create(team=chatbot.team, type=VoiceProviderType.openai_voice_engine)
    voice = SyntheticVoiceFactory.create(service="OpenAIVoiceEngine", voice_provider=provider)
    if activate:
        activate(openai_voice_engine_flag, chatbot.team)
        openai_voice_engine_flag.flush()

    response = client.patch(
        _url(chatbot),
        {"voice_provider_id": provider.id, "synthetic_voice_id": voice.id},
        format="json",
    )

    assert response.status_code == expected_status, response.content


@pytest.mark.django_db()
def test_omitting_the_voice_fields_leaves_them_untouched(client, chatbot):
    provider, voice = _aws_pair(chatbot)
    _store_voice(chatbot, provider, voice)

    client.patch(_url(chatbot), {"name": "Renamed"}, format="json")

    chatbot.refresh_from_db()
    assert (chatbot.voice_provider, chatbot.synthetic_voice) == (provider, voice)

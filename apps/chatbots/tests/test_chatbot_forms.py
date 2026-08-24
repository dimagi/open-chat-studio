import pytest
from django.test import RequestFactory

from apps.chatbots.forms import ChatbotForm, ChatbotSettingsForm
from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.service_providers.models import VoiceProviderType
from apps.teams.utils import set_current_team
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_chatbot_form_valid_data(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.team = team
    request.user = user
    set_current_team(team)
    form_data = {
        "name": "Test Chatbot",
        "description": "A chatbot for testing",
    }

    form = ChatbotForm(request, data=form_data)

    assert form.is_valid()
    experiment = form.save()
    assert Experiment.objects.filter(name="Test Chatbot", team=team).exists()
    assert experiment.pipeline is not None
    assert experiment.owner == user


@pytest.mark.django_db()
def test_chatbot_form_missing_name(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.team = team
    request.user = user

    form_data = {
        "name": "",  # Missing name
        "description": "A chatbot without a name",
    }

    form = ChatbotForm(request, data=form_data)

    assert not form.is_valid()
    assert "name" in form.errors


@pytest.mark.django_db()
def test_chatbot_form_pipeline_creation(team_with_users):
    team = team_with_users
    user = team.members.first()
    request = RequestFactory().get("/")
    request.user = user
    request.team = team
    form_data = {
        "name": "Chatbot with Pipeline",
        "description": "Testing pipeline creation",
    }
    set_current_team(team)
    form = ChatbotForm(request, data=form_data)

    assert form.is_valid()
    experiment = form.save()
    assert Pipeline.objects.filter(name="Chatbot with Pipeline", team=team).exists()
    assert experiment.pipeline is not None


@pytest.mark.django_db()
def test_chatbot_settings_form_consent_form_queryset_is_team_scoped(team_with_users):
    """Consent form choices in the settings form must be scoped to the current team and exclude versioned records."""
    team = team_with_users
    other_team = TeamFactory()
    user = team.members.first()

    own_consent = ConsentFormFactory(team=team)
    versioned_consent = ConsentFormFactory(team=team, working_version=own_consent)
    other_consent = ConsentFormFactory(team=other_team)

    request = RequestFactory().get("/")
    request.team = team
    request.user = user

    form = ChatbotSettingsForm(request)

    consent_ids = set(form.fields["consent_form"].queryset.values_list("id", flat=True))
    assert own_consent.id in consent_ids
    assert versioned_consent.id not in consent_ids
    assert other_consent.id not in consent_ids


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("entered", "cleaned"),
    [
        pytest.param("+27 82 000 0000", ["+27820000000"], id="spaces-inside-an-identifier-are-stripped"),
        pytest.param("a@example.com, b@example.com", ["a@example.com", "b@example.com"], id="entries-are-split"),
        pytest.param(
            "a@example.com,,b@example.com", ["a@example.com", "b@example.com"], id="empty-entries-are-dropped"
        ),
        pytest.param("a@example.com,   ", ["a@example.com"], id="whitespace-only-entries-are-dropped"),
        pytest.param("", [], id="an-empty-allowlist-stays-empty"),
    ],
)
def test_chatbot_settings_form_normalises_the_participant_allowlist(team_with_users, entered, cleaned):
    """`Experiment.is_participant_allowed` matches identifiers exactly, so an unstripped one is an
    allowlist that looks configured and admits nobody. The v2 write API shares this normalisation
    (`normalize_participant_allowlist`), so pinning it here pins both callers."""
    request = RequestFactory().get("/")
    request.team = team_with_users
    request.user = team_with_users.members.first()

    form = ChatbotSettingsForm(request, data={"name": "Bot", "participant_allowlist": entered})
    form.is_valid()

    assert form.cleaned_data["participant_allowlist"] == cleaned


@pytest.mark.django_db()
def test_chatbot_settings_form_offers_only_voices_a_provider_can_speak(team_with_users):
    """A voice is only speakable by a voice provider of its own type, so a voice whose service the
    team holds no provider for is not a choice the form may accept -- picking it gets a chatbot that
    falls silent when it tries to speak."""
    team = team_with_users
    aws = VoiceProviderFactory.create(team=team, type=VoiceProviderType.aws)
    speakable = SyntheticVoiceFactory.create(name="Joanna", service="AWS", voice_provider=aws)
    shared = SyntheticVoiceFactory.create(name="Matthew", service="AWS")
    unspeakable = SyntheticVoiceFactory.create(name="Amber", service="Azure")
    request = RequestFactory().get("/")
    request.team = team
    request.user = team.members.first()

    form = ChatbotSettingsForm(request)

    voice_ids = set(form.fields["synthetic_voice"].queryset.values_list("id", flat=True))
    assert {speakable.id, shared.id} <= voice_ids
    assert unspeakable.id not in voice_ids


@pytest.mark.django_db()
def test_chatbot_settings_form_accepts_a_voice_the_settings_page_offers(team_with_users):
    """The settings page builds its voice dropdown from every voice of the chosen provider's type,
    including the shared ones. Narrowing the form's own list must not turn one of those into a
    "select a valid choice" error on save."""
    team = team_with_users
    provider = VoiceProviderFactory.create(team=team, type=VoiceProviderType.aws)
    shared_voice = SyntheticVoiceFactory.create(name="Matthew", service="AWS")
    experiment = ExperimentFactory.create(team=team)
    request = RequestFactory().get("/")
    request.team = team
    request.user = team.members.first()
    set_current_team(team)

    form = ChatbotSettingsForm(
        request,
        data={
            "name": experiment.name,
            "voice_provider": provider.id,
            "synthetic_voice": shared_voice.id,
            "voice_response_behaviour": experiment.voice_response_behaviour,
        },
        instance=experiment,
    )

    assert form.is_valid(), form.errors
    assert form.save().synthetic_voice_id == shared_voice.id

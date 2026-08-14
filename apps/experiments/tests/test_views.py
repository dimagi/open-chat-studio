from contextlib import nullcontext as does_not_raise
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse

from apps.channels.api_channel import ApiChannel
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.const import EMBED_FLOW_SUCCESSOR_URL
from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    Participant,
    ParticipantData,
    VoiceResponseBehaviours,
)
from apps.teams.backends import add_user_to_team
from apps.utils.factories.experiment import (
    ConsentFormFactory,
    ExperimentFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory, UserFactory
from apps.utils.prompt import get_root_var, validate_prompt_variables


def _start_session(experiment, participant_identifier, participant_user=None, timezone=None):
    """Start a session the way the surviving chat API does."""
    return ApiChannel.start_new_session(
        experiment,
        experiment_channel=ExperimentChannel.objects.get_team_api_channel(experiment.team),
        participant_identifier=participant_identifier,
        participant_user=participant_user,
        timezone=timezone,
    )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_create_experiment_creates_first_version(client, team_with_users):
    user = team_with_users.members.first()
    consent_form = ConsentFormFactory.create(team=team_with_users)
    LlmProviderFactory.create(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "type": "llm",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm_provider": LlmProviderFactory.create(team=team_with_users).id,
        "llm_provider_model": LlmProviderModelFactory.create(team=team_with_users).id,
        "max_token_limit": 100,
        "voice_response_behaviour": VoiceResponseBehaviours.RECIPROCAL,
    }
    client.post(reverse("chatbots:new", args=[team_with_users.slug]), data=post_data)
    experiments = Experiment.objects.filter(owner=user).all()
    assert len(experiments) == 2
    working_verison = experiments.filter(working_version=None).first()
    versioned_exp = experiments.filter(version_number=1).first()
    assert working_verison is not None
    assert versioned_exp is not None
    assert versioned_exp.is_default_version


@pytest.mark.parametrize(
    ("tools", "source_material", "prompt_str", "expectation"),
    [
        (None, None, "You're an assistant", does_not_raise()),
        (None, "something", "You're an assistant", pytest.raises(ValidationError)),
        (None, "something", "Answer questions from this source: {source_material}", does_not_raise()),
        (None, None, "Answer questions from this source: {source_material}", pytest.raises(ValidationError)),
        (None, None, "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
        (None, "something", "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
        (None, "something", "Source material: {source_material} and {source_material}", pytest.raises(ValidationError)),
        (["one-off-reminder"], None, "", pytest.raises(ValidationError)),
        (["recurring-reminder"], None, "", pytest.raises(ValidationError)),
        (["delete-reminder"], None, "", pytest.raises(ValidationError)),
        (["move-scheduled-message-date"], None, "", pytest.raises(ValidationError)),
        (["move-scheduled-message-date"], None, "{current_datetime}", pytest.raises(ValidationError)),
        (["move-scheduled-message-date"], None, "{participant_data}", pytest.raises(ValidationError)),
        (["update-user-data"], None, "", pytest.raises(ValidationError)),
        (["one-off-reminder"], None, "{current_datetime}", does_not_raise()),
        (["recurring-reminder"], None, "{current_datetime}", does_not_raise()),
        (["delete-reminder"], None, "{participant_data}", does_not_raise()),
        (["move-scheduled-message-date"], None, "{participant_data},{current_datetime}", does_not_raise()),
        (["update-user-data"], None, "{participant_data}", does_not_raise()),
        (None, None, "{participant_data}", does_not_raise()),
        (None, None, "{participant_data.name}", does_not_raise()),
    ],
)
def test_prompt_variable_validation(tools, source_material, prompt_str, expectation):
    with expectation:
        validate_prompt_variables(
            {"source_material": source_material, "prompt_text": prompt_str, "tools": tools},
            prompt_key="prompt_text",
            known_vars={"source_material", "participant_data", "current_datetime"},
        )


@pytest.mark.parametrize(
    ("input_var", "expected_output"),
    [
        ("participant_data.name", "participant_data"),
        ("participant_data[0]", "participant_data"),
        ("participant_data", "participant_data"),
        ("current_datetime", "current_datetime"),
        ("source_material", "source_material"),
        ("source_material.a", "source_material.a"),
        ("other_var", "other_var"),
        ("other_var[1]", "other_var[1]"),
    ],
)
def test_get_root_var_returns_correct_root_variable(input_var, expected_output):
    assert get_root_var(input_var) == expected_output


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_new_participant_created_on_session_start(_trigger_mock, is_user):
    """For each new experiment session, a participant should be created and linked to the session"""
    identifier = "someone@example.com"
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    user = None
    if is_user:
        user = experiment.team.members.first()
        identifier = user.email

    session = _start_session(experiment, identifier, participant_user=user)

    assert Participant.objects.filter(team=experiment.team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=experiment.team).count() == 1
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_participant_reused_within_team(_trigger_mock, is_user):
    """Within a team, the same external chat id (or participant identifier) should result in the participant being
    reused, and not result in a new participant being created
    """
    experiment1 = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = _start_session(experiment1, identifier, participant_user=user)

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 1
    assert session.participant.identifier == identifier

    # user starts a second session in the same team
    experiment2 = ExperimentFactory.create(team=team)

    session = _start_session(experiment2, identifier, participant_user=user)

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_new_participant_created_for_different_teams(_trigger_mock, is_user):
    """A new participant should be created for each team when a user uses the same identifier"""
    experiment1 = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = _start_session(experiment1, identifier, participant_user=user)

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 1
    assert session.participant.identifier == identifier

    # user starts a second session in another team
    if is_user:
        new_team = TeamWithUsersFactory.create(member__user=user)
    else:
        new_team = TeamWithUsersFactory.create()

    experiment2 = ExperimentFactory.create(team=new_team)

    session = _start_session(experiment2, identifier, participant_user=user)

    assert Participant.objects.filter(team=new_team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=new_team).count() == 1

    # There should be two participants with identifier = identifier accross all teams
    assert Participant.objects.filter(identifier=identifier).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_participant_gets_user_when_they_signed_up(_trigger_mock):
    """When a non platform user starts a session, a participant without a user is created. When they then sign up
    and start another session, their participant user should be populated
    """
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    assert Participant.objects.filter(team=experiment.team).count() == 0
    email = "test@user.com"

    # Non platform user creates a session
    _start_session(experiment, email)
    participant = Participant.objects.get(team=experiment.team, identifier=email)
    assert participant.user is None

    # Let's create the user by creating another experiment
    user = UserFactory.create(email=email)
    add_user_to_team(experiment.team, user=user)
    # Now the platform user creates a session
    _start_session(experiment, email, participant_user=user)

    participant = Participant.objects.get(team=experiment.team, identifier=email)
    assert participant.user is not None


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url_name", "extra_kwargs", "method"),
    [
        pytest.param("experiments:start_session_public_embed", {}, "get", id="start-session"),
        pytest.param(
            "experiments:experiment_session_message_embed",
            {"session_id": "abc123", "version_number": 1},
            "post",
            id="send-message",
        ),
        pytest.param("experiments:poll_messages_embed", {"session_id": "abc123"}, "get", id="poll-messages"),
        pytest.param("chatbots:start_session_public_embed", {}, "get", id="chatbots-start-session"),
        pytest.param("chatbots:chatbot_chat_embed", {"session_id": "abc123"}, "get", id="chatbots-chat-ui"),
    ],
)
def test_legacy_embed_flow_urls_are_gone(url_name, extra_kwargs, method):
    """The legacy embed flow was removed (issue #3540); its URLs answer 410, never a silent 404.

    Legacy callers are cross-origin iframes posting without a CSRF token, so the stub must
    survive CSRF enforcement and stay renderable in a frame — otherwise they get a 403 or a
    blocked frame instead of the message pointing at the widget.
    """
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    url = reverse(
        url_name,
        kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id, **extra_kwargs},
    )
    client = Client(enforce_csrf_checks=True)
    response = getattr(client, method)(url)
    assert response.status_code == 410
    assert EMBED_FLOW_SUCCESSOR_URL in response.content.decode()
    assert "X-Frame-Options" not in response.headers


@pytest.mark.django_db()
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_timezone_saved_in_participant_data(_trigger_mock):
    """A participant's timezone data should be saved in all ParticipantData records"""
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    team = experiment.team
    experiment2 = ExperimentFactory.create(team=team)
    identifier = "someone@example.com"
    # Participants are keyed per platform, so this must match the platform `_start_session` uses.
    participant = Participant.objects.create(identifier=identifier, team=team, platform=ChannelPlatform.API)
    part_data1 = ParticipantData.objects.create(team=team, participant=participant, experiment=experiment)
    part_data2 = ParticipantData.objects.create(team=experiment2.team, participant=participant, experiment=experiment2)

    _start_session(experiment, identifier, timezone="Africa/Johannesburg")

    part_data1.refresh_from_db()
    part_data2.refresh_from_db()
    assert part_data1.data["timezone"] == "Africa/Johannesburg"
    assert part_data2.data["timezone"] == "Africa/Johannesburg"


@pytest.mark.django_db()
@mock.patch("apps.experiments.views.experiment.async_export_chat.delay")
def test_generate_chat_export_enqueues_serializable_query_params(delay_mock, experiment, client):
    """The task args must survive Celery's JSON serializer.

    A QueryDict is serialized into a plain dict, which has no ``.getlist()``, so the view
    passes the raw query string and the task rebuilds the QueryDict itself.
    """
    delay_mock.return_value = "task-123"  # rendered into the template as the progress task id
    current_url = "https://example.com/sessions?f_participant=alice&op_participant=equals"
    client.force_login(experiment.team.members.first())

    response = client.post(
        reverse("experiments:generate_chat_export", args=[experiment.team.slug, experiment.id]),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL=current_url,
    )

    assert response.status_code == 200
    _experiment_id, query_params, _time_zone = delay_mock.call_args.args
    assert isinstance(query_params, str)
    assert query_params == "f_participant=alice&op_participant=equals"

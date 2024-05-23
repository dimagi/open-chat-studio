from contextlib import nullcontext as does_not_raise
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from waffle.testutils import override_flag

from apps.experiments.models import AgentTools, Experiment, ExperimentSession, Participant, VoiceResponseBehaviours
from apps.experiments.views.experiment import ExperimentForm, _start_experiment_session, _validate_prompt_variables
from apps.teams.backends import add_user_to_team
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory, SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory, UserFactory


def test_create_experiment_success(db, client, team_with_users):
    user = team_with_users.members.first()
    source_material = SourceMaterialFactory(team=team_with_users)
    consent_form = ConsentFormFactory(team=team_with_users)
    LlmProviderFactory(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "description": "Some description",
        "type": "llm",
        "prompt_text": "You are a helpful assistant",
        "source_material": source_material.id if source_material else "",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm_provider": LlmProviderFactory(team=team_with_users).id,
        "llm": "gpt-3.5",
        "max_token_limit": 100,
        "voice_response_behaviour": VoiceResponseBehaviours.RECIPROCAL,
        "tools": [AgentTools.ONE_OFF_REMINDER],
    }

    response = client.post(reverse("experiments:new", args=[team_with_users.slug]), data=post_data)
    assert response.status_code == 302, response.context.form.errors
    experiment = Experiment.objects.filter(owner=user).first()
    assert experiment is not None
    experiment.tool_resources.get(tool_name=AgentTools.ONE_OFF_REMINDER)


@override_flag("assistants", active=True)
@pytest.mark.parametrize(
    ("with_assistant", "with_prompt", "with_llm_provider", "with_llm_model", "errors"),
    [
        (True, False, False, False, {}),
        (False, True, True, True, {}),
        (False, False, True, True, {"prompt_text"}),
        (False, True, False, True, {"llm_provider"}),
        (False, True, True, False, {"llm"}),
    ],
)
def test_experiment_form_with_assistants(
    with_assistant, with_prompt, with_llm_provider, with_llm_model, errors, db, team_with_users
):
    assistant = OpenAiAssistantFactory(team=team_with_users)
    request = mock.Mock()
    request.team = team_with_users
    llm_provider = LlmProviderFactory(team=team_with_users)
    form = ExperimentForm(
        request,
        data={
            "name": "some name",
            "type": "assistant" if with_assistant else "llm",
            "assistant": assistant.id if with_assistant else None,
            "prompt_text": "text" if with_prompt else None,
            "llm_provider": llm_provider.id if with_llm_provider else None,
            "llm": "gpt4" if with_llm_model else None,
            "temperature": 0.7,
            "max_token_limit": 10,
            "consent_form": ConsentFormFactory(team=team_with_users).id,
            "voice_response_behaviour": VoiceResponseBehaviours.RECIPROCAL,
        },
    )
    assert form.is_valid() == bool(not errors), form.errors
    for error in errors:
        assert error in form.errors


@pytest.mark.parametrize(
    ("source_material", "prompt_str", "expectation"),
    [
        (None, "You're an assistant", does_not_raise()),
        ("something", "You're an assistant", does_not_raise()),
        ("something", "Answer questions from this source: {source_material}", does_not_raise()),
        (None, "Answer questions from this source: {source_material}", pytest.raises(ValidationError)),
        (None, "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
        ("something", "Answer questions from this source: {bob}", pytest.raises(ValidationError)),
    ],
)
def test_prompt_variable_validation(source_material, prompt_str, expectation):
    with expectation:
        _validate_prompt_variables(
            {
                "source_material": source_material,
                "prompt_text": prompt_str,
            }
        )


@pytest.mark.django_db()
@mock.patch("apps.experiments.models.SyntheticVoice.get_for_team")
def test_form_fields(_get_for_team_mock):
    path = settings.BASE_DIR / "templates" / "experiments" / "experiment_form.html"
    form_html = path.read_text()
    request = mock.Mock()
    for field in ExperimentForm(request).fields:
        assert field in form_html, f"{field} missing from 'experiment_form.html' template"


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_new_participant_created_on_session_start(_trigger_mock, is_user):
    """For each new experiment session, a participant should be created and linked to the session"""
    identifier = "someone@example.com"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    channel = ExperimentChannelFactory(experiment=experiment)
    user = None
    if is_user:
        user = experiment.team.members.first()
        identifier = user.email

    session = _start_experiment_session(
        experiment,
        experiment_channel=channel,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=experiment.team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=experiment.team).count() == 1
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_start_session_public_with_emtpy_identifier(_trigger_mock, is_user, client):
    """Identifiers can be empty if we choose not to capture it. In this case, use the logged in user's email or in
    the case where it's an external user, use a UUID as the identifier"""
    experiment = ExperimentFactory(team=TeamWithUsersFactory(), consent_form__capture_identifier=False)
    assert Participant.objects.filter(team=experiment.team).count() == 0

    user = None
    if is_user:
        user = experiment.team.members.first()
        client.login(username=user.username, password="password")

    post_data = {"identifier": "", "consent_agreement": True, "experiment_id": str(experiment.id), "participant_id": ""}

    url = reverse(
        "experiments:start_session_public",
        kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
    )
    client.post(url, data=post_data)
    assert Participant.objects.filter(team=experiment.team).count() == 1
    if is_user:
        assert Participant.objects.filter(team=experiment.team, identifier=user.email).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_participant_reused_within_team(_trigger_mock, is_user):
    """Within a team, the same external chat id (or participant identifier) should result in the participant being
    reused, and not result in a new participant being created
    """
    experiment1 = ExperimentFactory(team=TeamWithUsersFactory())
    channel1 = ExperimentChannelFactory(experiment=experiment1)
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = _start_experiment_session(
        experiment1,
        experiment_channel=channel1,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 1
    assert session.participant.identifier == identifier

    # user starts a second session in the same team
    experiment2 = ExperimentFactory(team=team)
    channel2 = ExperimentChannelFactory(experiment=experiment2)

    session = _start_experiment_session(
        experiment2,
        experiment_channel=channel2,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_new_participant_created_for_different_teams(_trigger_mock, is_user):
    """A new participant should be created for each team when a user uses the same identifier"""
    experiment1 = ExperimentFactory(team=TeamWithUsersFactory())
    channel1 = ExperimentChannelFactory(experiment=experiment1)
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = _start_experiment_session(
        experiment1,
        experiment_channel=channel1,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 1
    assert session.participant.identifier == identifier

    # user starts a second session in another team
    if is_user:
        new_team = TeamWithUsersFactory(member__user=user)
    else:
        new_team = TeamWithUsersFactory()

    experiment2 = ExperimentFactory(team=new_team)
    channel2 = ExperimentChannelFactory(experiment=experiment2)

    session = _start_experiment_session(
        experiment2,
        experiment_channel=channel2,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=new_team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=new_team).count() == 1

    # There should be two participants with identifier = identifier accross all teams
    assert Participant.objects.filter(identifier=identifier).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_participant_gets_user_when_they_signed_up(_trigger_mock, client):
    """When a non platform user starts a session, a participant without a user is created. When they then sign up
    and start another session, their participant user should be populated
    """
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    assert Participant.objects.filter(team=experiment.team).count() == 0
    email = "test@user.com"
    post_data = {
        "identifier": email,
        "consent_agreement": True,
        "experiment_id": str(experiment.id),
        "participant_id": "",
    }
    url = reverse(
        "experiments:start_session_public",
        kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
    )

    # Non platform user creates a session
    client.post(url, data=post_data)
    participant = Participant.objects.get(team=experiment.team, identifier=email)
    assert participant.user is None

    # Let's create the user by creating another experiment
    user = UserFactory(email=email)
    add_user_to_team(experiment.team, user=user)
    # Now the platform user creates a session
    client.login(username=user.username, password="password")
    client.post(url, data=post_data)

    participant = Participant.objects.get(team=experiment.team, identifier=email)
    assert participant.user is not None


@pytest.mark.django_db()
@mock.patch("apps.experiments.views.experiment.enqueue_static_triggers")
def test_user_email_used_for_participant_identifier(_trigger_mock, client):
    """With the `capture_identifier` field enabled on the consent record, logged in users' consent form will
    not contain the `identifier` field, so we pass it as initial data to the form. This test simulates a logged
    in user submitting the consent form
    """
    experiment = ExperimentFactory(team=TeamWithUsersFactory(), consent_form__capture_identifier=True)
    assert Participant.objects.filter(team=experiment.team).count() == 0

    user = experiment.team.members.first()
    client.login(username=user.username, password="password")

    post_data = {"consent_agreement": True, "experiment_id": str(experiment.id), "participant_id": ""}

    url = reverse(
        "experiments:start_session_public",
        kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
    )
    client.post(url, data=post_data)
    assert Participant.objects.filter(team=experiment.team, identifier=user.email).exists()

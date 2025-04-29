from contextlib import nullcontext as does_not_raise
from io import BytesIO
from unittest import mock

import jwt
import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import reverse
from waffle.testutils import override_flag

from apps.chat.channels import WebChannel
from apps.chat.models import Chat
from apps.experiments.models import (
    AgentTools,
    Experiment,
    ExperimentSession,
    Participant,
    ParticipantData,
    VoiceResponseBehaviours,
)
from apps.experiments.views.experiment import (
    ExperimentForm,
    ExperimentTableView,
    _verify_user_or_start_session,
)
from apps.teams.backends import add_user_to_team
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import (
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
from apps.utils.factories.team import TeamWithUsersFactory, UserFactory
from apps.utils.prompt import get_root_var, validate_prompt_variables


@pytest.mark.django_db()
def test_create_experiment_success(client, team_with_users):
    user = team_with_users.members.first()
    source_material = SourceMaterialFactory(team=team_with_users)
    consent_form = ConsentFormFactory(team=team_with_users)
    LlmProviderFactory(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "description": "Some description",
        "type": "llm",
        "prompt_text": """
            You are a helpful assistant. The current date time is {current_datetime}.
            Participant data: {participant_data}.
            Source material: {source_material}.
        """,
        "source_material": source_material.id if source_material else "",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm_provider": LlmProviderFactory(team=team_with_users).id,
        "llm_provider_model": LlmProviderModelFactory(team=team_with_users).id,
        "max_token_limit": 100,
        "voice_response_behaviour": VoiceResponseBehaviours.RECIPROCAL,
        "tools": [AgentTools.ONE_OFF_REMINDER],
    }

    response = client.post(reverse("experiments:new", args=[team_with_users.slug]), data=post_data)
    assert response.status_code == 302, response.context["form"].errors
    experiment = Experiment.objects.filter(owner=user).first()
    assert experiment is not None
    assert experiment.tools == [AgentTools.ONE_OFF_REMINDER]


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_create_experiment_creates_first_version(client, team_with_users):
    user = team_with_users.members.first()
    consent_form = ConsentFormFactory(team=team_with_users)
    LlmProviderFactory(team=team_with_users)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "type": "llm",
        "prompt_text": "You are a helpful assistant.",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm_provider": LlmProviderFactory(team=team_with_users).id,
        "llm_provider_model": LlmProviderModelFactory(team=team_with_users).id,
        "max_token_limit": 100,
        "voice_response_behaviour": VoiceResponseBehaviours.RECIPROCAL,
    }
    client.post(reverse("experiments:new", args=[team_with_users.slug]), data=post_data)
    experiments = Experiment.objects.filter(owner=user).all()
    assert len(experiments) == 2
    working_verison = experiments.filter(working_version=None).first()
    versioned_exp = experiments.filter(version_number=1).first()
    assert working_verison is not None
    assert versioned_exp is not None
    assert versioned_exp.is_default_version


@override_flag("assistants", active=True)
@pytest.mark.parametrize(
    ("with_assistant", "with_prompt", "with_llm_provider", "with_llm_model", "errors"),
    [
        (True, False, False, False, {}),
        (False, True, True, True, {}),
        (False, False, True, True, {"prompt_text"}),
        (False, True, False, True, {"llm_provider"}),
        (False, True, True, False, {"llm_provider_model"}),
    ],
)
def test_experiment_form_with_assistants(
    with_assistant, with_prompt, with_llm_provider, with_llm_model, errors, db, team_with_users
):
    assistant = OpenAiAssistantFactory(team=team_with_users)
    request = mock.Mock()
    request.team = team_with_users
    llm_provider = LlmProviderFactory(team=team_with_users)
    llm_provider_model = LlmProviderModelFactory(team=team_with_users, type=llm_provider.type)
    form = ExperimentForm(
        request,
        data={
            "name": "some name",
            "type": "assistant" if with_assistant else "llm",
            "assistant": assistant.id if with_assistant else None,
            "prompt_text": "text" if with_prompt else None,
            "llm_provider": llm_provider.id if with_llm_provider else None,
            "llm_provider_model": llm_provider_model.id if with_llm_model else None,
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
@mock.patch("apps.experiments.forms.initialize_form_for_custom_actions", mock.Mock())
@mock.patch("apps.experiments.models.SyntheticVoice.get_for_team", mock.Mock())
def test_form_fields():
    path = settings.BASE_DIR / "templates" / "experiments" / "experiment_form.html"
    form_html = path.read_text()
    request = mock.Mock()
    for field in ExperimentForm(request).fields:
        assert field in form_html, f"{field} missing from 'experiment_form.html' template"


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.chat.channels.enqueue_static_triggers")
def test_new_participant_created_on_session_start(_trigger_mock, is_user):
    """For each new experiment session, a participant should be created and linked to the session"""
    identifier = "someone@example.com"
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    user = None
    if is_user:
        user = experiment.team.members.first()
        identifier = user.email

    session = WebChannel.start_new_session(
        experiment,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=experiment.team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=experiment.team).count() == 1
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.chat.channels.enqueue_static_triggers")
def test_participant_reused_within_team(_trigger_mock, is_user):
    """Within a team, the same external chat id (or participant identifier) should result in the participant being
    reused, and not result in a new participant being created
    """
    experiment1 = ExperimentFactory(team=TeamWithUsersFactory())
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = WebChannel.start_new_session(
        experiment1,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 1
    assert session.participant.identifier == identifier

    # user starts a second session in the same team
    experiment2 = ExperimentFactory(team=team)

    session = WebChannel.start_new_session(
        experiment2,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=team).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@pytest.mark.parametrize("is_user", [False, True])
@mock.patch("apps.chat.channels.enqueue_static_triggers")
def test_new_participant_created_for_different_teams(_trigger_mock, is_user):
    """A new participant should be created for each team when a user uses the same identifier"""
    experiment1 = ExperimentFactory(team=TeamWithUsersFactory())
    team = experiment1.team
    identifier = "someone@example.com"
    user = None
    if is_user:
        user = team.members.first()
        identifier = user.email

    session = WebChannel.start_new_session(
        experiment1,
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

    session = WebChannel.start_new_session(
        experiment2,
        participant_user=user,
        participant_identifier=identifier,
    )

    assert Participant.objects.filter(team=new_team, identifier=identifier).count() == 1
    assert ExperimentSession.objects.filter(team=new_team).count() == 1

    # There should be two participants with identifier = identifier accross all teams
    assert Participant.objects.filter(identifier=identifier).count() == 2
    assert session.participant.identifier == identifier


@pytest.mark.django_db()
@mock.patch("apps.chat.channels.enqueue_static_triggers")
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
@mock.patch("apps.chat.channels.enqueue_static_triggers")
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


@pytest.mark.django_db()
@mock.patch("apps.chat.channels.enqueue_static_triggers")
def test_timezone_saved_in_participant_data(_trigger_mock):
    """A participant's timezone data should be saved in all ParticipantData records"""
    experiment = ExperimentFactory(team=TeamWithUsersFactory())
    team = experiment.team
    experiment2 = ExperimentFactory(team=team)
    identifier = "someone@example.com"
    participant = Participant.objects.create(identifier=identifier, team=team, platform="web")
    part_data1 = ParticipantData.objects.create(team=team, participant=participant, experiment=experiment)
    part_data2 = ParticipantData.objects.create(team=experiment2.team, participant=participant, experiment=experiment2)

    WebChannel.start_new_session(
        experiment,
        participant_identifier=identifier,
        timezone="Africa/Johannesburg",
    )

    part_data1.refresh_from_db()
    part_data2.refresh_from_db()
    assert part_data1.data["timezone"] == "Africa/Johannesburg"
    assert part_data2.data["timezone"] == "Africa/Johannesburg"


@pytest.mark.django_db()
@pytest.mark.parametrize("version", [Experiment.DEFAULT_VERSION_NUMBER, 1])
@mock.patch("apps.chat.channels.enqueue_static_triggers", mock.Mock())
@mock.patch("apps.experiments.views.experiment.get_response_for_webchat_task.delay")
def test_experiment_session_message_view_creates_files(delay_mock, version, experiment, client):
    task = mock.Mock()
    task.task_id = 1
    delay_mock.return_value = task
    session = ExperimentSessionFactory(experiment=experiment, participant=ParticipantFactory(user=experiment.owner))
    url_kwargs = {
        "team_slug": experiment.team.slug,
        "experiment_id": experiment.public_id,
        "session_id": session.external_id,
        "version_number": version,
    }
    url = reverse("experiments:experiment_session_message", kwargs=url_kwargs)

    client.force_login(experiment.owner)
    file_search_file = BytesIO(b"some content")
    file_search_file.name = "fs.text"
    code_interpreter_file = BytesIO(b"some content")
    code_interpreter_file.name = "ci.text"
    data = {"message": "Hi there", "file_search": [file_search_file], "code_interpreter": [code_interpreter_file]}
    client.post(url, data=data)
    # Check if tool resources were created with the files
    ci_resource = session.chat.attachments.get(tool_type="code_interpreter")
    assert ci_resource.files.filter(name="ci.text").exists()
    fs_resource = session.chat.attachments.get(tool_type="file_search")
    assert fs_resource.files.filter(name="fs.text").exists()


class TestExperimentTableView:
    def test_get_queryset(self, experiment):
        team = experiment.team
        experiment.create_new_version()
        archived_working = ExperimentFactory(team=team)
        archived_version = archived_working.create_new_version()
        archived_version.is_archived = archived_working.is_archived = True
        archived_version.save()
        archived_working.save()
        assert Experiment.objects.get_all().count() == 4

        request = RequestFactory().get(reverse("experiments:table", args=[team.slug]))
        request.team = team
        view = ExperimentTableView()
        view.request = request
        assert list(view.get_queryset().all()) == [experiment]


@pytest.mark.django_db()
@pytest.mark.parametrize("version_number", [1, 2])
def test_start_authed_web_session_with_version(version_number, client):
    team = TeamWithUsersFactory()
    working_experiment = ExperimentFactory(team=team)
    working_experiment.create_new_version()

    client.force_login(working_experiment.team.members.first())
    url = reverse(
        "experiments:start_authed_web_session",
        kwargs={
            "team_slug": working_experiment.team.slug,
            "experiment_id": working_experiment.id,
            "version_number": version_number,
        },
    )

    response = client.post(url, data={})
    assert response.status_code == 302
    assert working_experiment.sessions.count() == 1
    expected_chat_metadata = {Chat.MetadataKeys.EXPERIMENT_VERSION: version_number}
    assert working_experiment.sessions.first().chat.metadata == expected_chat_metadata


@pytest.mark.django_db()
class TestPublicSessions:
    @pytest.mark.parametrize("is_user", [False, True])
    @mock.patch("apps.chat.channels.enqueue_static_triggers")
    def test_start_session_public_with_emtpy_identifier(self, _trigger_mock, is_user, client):
        """Identifiers can be empty if we choose not to capture it. In this case, use the logged in user's email or in
        the case where it's an external user, use a UUID as the identifier"""
        experiment = ExperimentFactory(team=TeamWithUsersFactory(), consent_form__capture_identifier=False)
        assert Participant.objects.filter(team=experiment.team).count() == 0

        user = None
        if is_user:
            user = experiment.team.members.first()
            client.login(username=user.username, password="password")

        post_data = {
            "identifier": "",
            "consent_agreement": True,
            "experiment_id": str(experiment.id),
            "participant_id": "",
        }

        url = reverse(
            "experiments:start_session_public",
            kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
        )
        client.post(url, data=post_data)
        assert Participant.objects.filter(team=experiment.team).count() == 1
        if is_user:
            assert Participant.objects.filter(team=experiment.team, identifier=user.email).exists()

    @pytest.mark.parametrize(("capture_identifier", "expect_user_verified"), [(True, True), (False, False)])
    @mock.patch("apps.experiments.views.experiment._verify_user_or_start_session")
    def test_user_is_verified_if_identifier_is_captured_and_participant_data_injected(
        self, verify_user, capture_identifier, expect_user_verified, client
    ):
        verify_user.return_value = HttpResponse()
        prompt = "This is data: {participant_data}"
        experiment = ExperimentFactory(
            team=TeamWithUsersFactory(), consent_form__capture_identifier=capture_identifier, prompt_text=prompt
        )
        post_data = {
            "identifier": "someone@gmail.com",
            "consent_agreement": True,
            "experiment_id": str(experiment.id),
            "participant_id": "",
        }

        url = reverse(
            "experiments:start_session_public",
            kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
        )
        client.post(url, data=post_data)
        if expect_user_verified:
            verify_user.assert_called()
        else:
            verify_user.assert_not_called()

    @mock.patch("apps.experiments.views.experiment._record_consent_and_redirect")
    def test_do_not_verify_authenticated_users(self, record_consent_and_redirect_mock, request):
        experiment_session = ExperimentSessionFactory()
        request.user = experiment_session.experiment.owner

        _verify_user_or_start_session("something", request, experiment_session.experiment, experiment_session)
        record_consent_and_redirect_mock.assert_called()

    @pytest.mark.parametrize(("participant_match"), [True, False])
    @mock.patch("apps.experiments.models.ExperimentSession.requires_participant_data")
    @mock.patch("apps.experiments.views.experiment.send_chat_link_email")
    @mock.patch("apps.experiments.views.experiment._record_consent_and_redirect")
    @mock.patch("apps.experiments.views.experiment.get_chat_session_access_cookie_data")
    def test_user_has_session_cookie(
        self,
        get_chat_session_access_cookie_data,
        record_consent_and_redirect_mock,
        send_chat_link_email,
        requires_participant_data,
        participant_match,
        request,
    ):
        """
        When a signed-out user wants to chat to a bot and has a session cookie from a prior chat, the following
        scenarios are expected:
        - If the specified email match that of the participant in the session, the user's email should not be verified
            again.
        - If the specified email do not match that of the participant in the session, it should be verified
        """
        requires_participant_data.return_value = True
        request_user = mock.Mock()
        request_user.is_authenticated = False
        request.user = request_user
        experiment_session = ExperimentSessionFactory()
        participant = experiment_session.participant
        get_chat_session_access_cookie_data.return_value = {
            "participant_id": participant.id if participant_match else participant.id + 1
        }
        _verify_user_or_start_session(
            identifier=participant.identifier,
            request=request,
            experiment=experiment_session.experiment,
            session=experiment_session,
        )

        if participant_match:
            record_consent_and_redirect_mock.assert_called()
            send_chat_link_email.assert_not_called()
        else:
            record_consent_and_redirect_mock.assert_not_called()
            send_chat_link_email.assert_called()

    @pytest.mark.parametrize(("participant_data_injected"), [True, False])
    @mock.patch("apps.experiments.views.experiment._record_consent_and_redirect")
    @mock.patch("apps.experiments.views.experiment.send_chat_link_email")
    @mock.patch("apps.experiments.views.experiment.get_chat_session_access_cookie_data")
    def test_user_does_not_have_session_cookie(
        self,
        get_chat_session_access_cookie_data,
        send_chat_link_email,
        _record_consent_and_redirect,
        participant_data_injected,
        request,
    ):
        """
        When a signed-out user wants to chat to a bot and does not have a session cookie from a prior chat, we should
        verify the specified email first.
        """
        get_chat_session_access_cookie_data.return_value = None
        prompt = "Data: {participant_data}" if participant_data_injected else "Data"
        request_user = mock.Mock()
        request_user.is_authenticated = False
        request.user = request_user
        experiment_session = ExperimentSessionFactory(experiment__prompt_text=prompt)
        _verify_user_or_start_session(
            identifier="someone@gmail.com",
            request=request,
            experiment=experiment_session.experiment,
            session=experiment_session,
        )
        if participant_data_injected:
            _record_consent_and_redirect.assert_not_called()
            send_chat_link_email.assert_called()
        else:
            send_chat_link_email.assert_not_called()
            _record_consent_and_redirect.assert_called()


@pytest.mark.django_db()
class TestVerifyPublicChatToken:
    @override_settings(SECRET_KEY="test_key")
    @mock.patch("apps.experiments.views.experiment._record_consent_and_redirect")
    def test_valid_token_redirects_to_chat(self, record_consent_and_redirect, client):
        record_consent_and_redirect.return_value = HttpResponse()
        session = ExperimentSessionFactory(experiment__pre_survey=None)
        experiment = session.experiment
        token = jwt.encode(
            {
                "session": str(session.external_id),
            },
            "test_key",
            algorithm="HS256",
        )
        client.get(
            reverse("experiments:verify_public_chat_token", args=(session.team.slug, experiment.public_id, token))
        )
        record_consent_and_redirect.assert_called()

    @mock.patch("apps.experiments.views.experiment._record_consent_and_redirect")
    def test_invalid_token_redirects_to_consent_form(self, record_consent_and_redirect, experiment, client):
        team_slug = experiment.team.slug
        token = "blah"
        expected_redirect_url = reverse("experiments:start_session_public", args=(team_slug, experiment.public_id))
        response = client.get(
            reverse("experiments:verify_public_chat_token", args=(team_slug, experiment.public_id, token))
        )
        assert response.url == expected_redirect_url
        record_consent_and_redirect.assert_not_called()


@pytest.mark.django_db()
class TestCreateExperimentVersionView:
    @pytest.mark.parametrize("in_sync_with_openai", [True, False])
    @mock.patch("apps.experiments.views.experiment.messages")
    @mock.patch("apps.experiments.views.experiment.async_create_experiment_version.delay")
    def test_create_version_with_assistant(self, delay, messages, in_sync_with_openai, client):
        delay.return_value = "a7a82d12-0abe-4466-92c7-95e4ed8eaf5c"
        team = TeamWithUsersFactory()
        experiment = ExperimentFactory(assistant=OpenAiAssistantFactory(), owner=team.members.first(), team=team)
        client.force_login(experiment.owner)
        post_data = {"version_description": "Some description", "is_default_version": True}
        url = reverse("experiments:create_version", args=[experiment.team.slug, experiment.id])

        with mock.patch("apps.experiments.views.CreateExperimentVersion._is_assistant_out_of_sync") as out_of_sync:
            out_of_sync.return_value = not in_sync_with_openai
            client.post(url, data=post_data)

        if in_sync_with_openai:
            expected_message = "Creating new version. This might take a few minutes."
            messages.success.assert_called_with(mock.ANY, expected_message)
            assert delay.called is True
        else:
            expected_message = "Assistant is out of sync with OpenAI. Please update the assistant first."
            messages.error.assert_called_with(mock.ANY, expected_message)
            assert delay.called is False

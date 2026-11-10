import json
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform
from apps.channels.tests.message_examples import api_messages
from apps.experiments.models import ExperimentSession
from apps.moderation.enforcement import BLOCKED_MESSAGE
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

BLOCKED_BODY = {"code": "participant_blocked", "detail": BLOCKED_MESSAGE}


@pytest.fixture()
def experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


def _block(team, identifier, platform, user=None):
    DeniedParticipantFactory.create(
        team=team, participant__identifier=identifier, participant__platform=platform, participant__user=user
    )


def _create_api_session(experiment, participant):
    client = ApiTestClient(experiment.team.members.first(), experiment.team)
    return client.post(
        reverse("api:session-list"),
        data={"experiment": experiment.public_id, "participant": participant},
        format="json",
    )


def _start_widget_session(client, experiment, user):
    client.force_login(user)
    return client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": str(experiment.public_id), "session_data": {}, "participant_remote_id": user.email},
        content_type="application/json",
    )


@pytest.mark.django_db()
class TestWidgetStartSession:
    def test_blocked_participant_gets_403(self, client, experiment):
        user = experiment.team.members.first()
        _block(experiment.team, user.email, ChannelPlatform.API, user=user)

        response = _start_widget_session(client, experiment, user)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
class TestApiSessionCreate:
    def test_blocked_participant_gets_403(self, experiment):
        _block(experiment.team, "jack bean", ChannelPlatform.API)

        response = _create_api_session(experiment, "jack bean")

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "url_name", [pytest.param("api:trigger_bot", id="v1"), pytest.param("api:v2:trigger_bot", id="v2")]
)
@patch("apps.api.views.channels.trigger_bot_message_task")
class TestTriggerBot:
    def _post(self, experiment, url_name):
        ExperimentChannelFactory.create(
            team=experiment.team,
            experiment=experiment,
            platform=ChannelPlatform.EMAIL,
            extra_data={"email_address": "bot@chat.openchatstudio.com"},
        )
        client = ApiTestClient(experiment.team.members.first(), experiment.team)
        data = {
            "identifier": "user@example.com",
            "platform": ChannelPlatform.EMAIL,
            "experiment": str(experiment.public_id),
            "prompt_text": "Say hello",
        }
        return client.post(reverse(url_name), json.dumps(data), content_type="application/json")

    def test_blocked_participant_gets_403_and_nothing_is_queued(self, trigger_bot_message_task, url_name, experiment):
        _block(experiment.team, "user@example.com", ChannelPlatform.EMAIL)

        response = self._post(experiment, url_name)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        trigger_bot_message_task.delay_on_commit.assert_not_called()
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@patch("apps.api.views.chat.get_response_for_webchat_task")
class TestWidgetSendMessage:
    def _send(self, session):
        url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
        return APIClient().post(url, data={"message": "hi"}, format="json")

    def test_blocked_participant_gets_403_and_nothing_is_queued(self, task, experiment):
        session = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
        DeniedParticipantFactory.create(team=session.team, participant=session.participant)

        response = self._send(session)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        task.delay.assert_not_called()


@pytest.mark.django_db()
@patch("apps.chat.bots.PipelineBot.process_input")
class TestApiNewMessage:
    def _send(self, experiment, user):
        return ApiTestClient(user, experiment.team).post(
            reverse("channels:new_api_message", kwargs={"experiment_id": experiment.public_id}),
            api_messages.text_message(),
            content_type="application/json",
        )

    def test_blocked_participant_gets_403(self, process_input, experiment):
        user = experiment.team.members.first()
        _block(experiment.team, user.email, ChannelPlatform.API, user=user)

        response = self._send(experiment, user)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        process_input.assert_not_called()
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@patch("apps.chat.bots.PipelineBot.process_input")
def test_openai_chat_completions_blocked_participant_gets_403(process_input, experiment):
    _block(experiment.team, "jack bean", ChannelPlatform.API)
    client = ApiTestClient(experiment.team.members.first(), experiment.team)
    data = {"messages": [{"role": "user", "content": "hi"}], "user": "jack bean"}

    response = client.post(
        reverse("api:openai-chat-completions", kwargs={"experiment_id": experiment.public_id}),
        json.dumps(data),
        content_type="application/json",
    )

    assert response.status_code == 403
    assert response.json() == BLOCKED_BODY
    process_input.assert_not_called()

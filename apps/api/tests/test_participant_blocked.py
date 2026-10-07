import json
from unittest.mock import patch

import pytest
from django.urls import reverse
from waffle.testutils import override_flag

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.moderation.enforcement import BLOCKED_MESSAGE
from apps.teams.flags import Flags
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

FLAG = Flags.ABUSE_DETECTION.slug
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
    @override_flag(FLAG, active=True)
    def test_blocked_participant_gets_403(self, client, experiment):
        user = experiment.team.members.first()
        _block(experiment.team, user.email, ChannelPlatform.API, user=user)

        response = _start_widget_session(client, experiment, user)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()

    @override_flag(FLAG, active=False)
    def test_flag_off_starts_the_session(self, client, experiment):
        user = experiment.team.members.first()
        _block(experiment.team, user.email, ChannelPlatform.API, user=user)

        response = _start_widget_session(client, experiment, user)

        assert response.status_code == 201, response.json()


@pytest.mark.django_db()
class TestApiSessionCreate:
    @override_flag(FLAG, active=True)
    def test_blocked_participant_gets_403(self, experiment):
        _block(experiment.team, "jack bean", ChannelPlatform.API)

        response = _create_api_session(experiment, "jack bean")

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()

    @override_flag(FLAG, active=False)
    def test_flag_off_creates_the_session(self, experiment):
        _block(experiment.team, "jack bean", ChannelPlatform.API)

        response = _create_api_session(experiment, "jack bean")

        assert response.status_code == 201, response.json()


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

    @override_flag(FLAG, active=True)
    def test_blocked_participant_gets_403_and_nothing_is_queued(self, trigger_bot_message_task, url_name, experiment):
        _block(experiment.team, "user@example.com", ChannelPlatform.EMAIL)

        response = self._post(experiment, url_name)

        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
        trigger_bot_message_task.delay_on_commit.assert_not_called()
        assert not ExperimentSession.objects.filter(experiment=experiment).exists()

    @override_flag(FLAG, active=False)
    def test_flag_off_queues_the_message(self, trigger_bot_message_task, url_name, experiment):
        _block(experiment.team, "user@example.com", ChannelPlatform.EMAIL)

        response = self._post(experiment, url_name)

        assert response.status_code == 200, response.json()
        trigger_bot_message_task.delay_on_commit.assert_called_once()

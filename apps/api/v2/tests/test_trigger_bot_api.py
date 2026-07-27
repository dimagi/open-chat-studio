import json
import os
import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import resolve, reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient
from apps.utils.tests.langchain import mock_llm


@pytest.fixture()
def experiment(db):
    exp = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    LlmProviderFactory.create(team=exp.team)
    return exp


def _setup_connect_participant_data(experiment, connect_id, system_metadata, encryption_key=None):
    participant = ParticipantFactory.create(
        team=experiment.team, identifier=connect_id, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    return ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata=system_metadata,
        encryption_key=encryption_key or "",
    )


def test_trigger_bot_url_reverses():
    assert reverse("api:v2:trigger_bot") == "/api/v2/trigger_bot/"


def test_trigger_bot_url_resolves():
    assert resolve("/api/v2/trigger_bot/").url_name == "trigger_bot"


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch("apps.channels.connect_channel.CommCareConnectClient")
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_trigger_bot_returns_connect_channel_data(
    ConnectClient, experiment, auth_method, django_capture_on_commit_callbacks
):
    """For a CommCare Connect channel, ``channel.data`` carries the Connect ``external_channel_id``."""
    connect_id = uuid.uuid4().hex
    commcare_connect_channel_id = uuid.uuid4().hex
    encryption_key = os.urandom(32).hex()
    participant_data = _setup_connect_participant_data(
        experiment,
        connect_id=connect_id,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id, "consent": True},
        encryption_key=encryption_key,
    )
    ExperimentChannelFactory.create(
        team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team, auth_method=auth_method)
    data = {
        "identifier": connect_id,
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "experiment": str(experiment.public_id),
        "prompt_text": "Tell the user to take a break",
    }
    url = reverse("api:v2:trigger_bot")
    with mock_llm(["Time to take a break"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200
    session = ExperimentSession.objects.get(participant=participant_data.participant, experiment=experiment)
    response_data = response.json()
    assert response_data["session_id"] == str(session.external_id)
    assert response_data["team"] == {"name": experiment.team.name, "slug": experiment.team.slug}
    assert f"/api/sessions/{session.external_id}/" in response_data["url"]
    assert response_data["channel"] == {
        "platform": ChannelPlatform.COMMCARE_CONNECT,
        "data": {"external_channel_id": commcare_connect_channel_id},
    }


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_trigger_bot_non_connect_channel_has_empty_data(experiment, django_capture_on_commit_callbacks):
    """Non-Connect platforms report an empty ``channel.data``."""
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)
    data = {
        "identifier": "user@example.com",
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        "prompt_text": "Say hello",
    }
    url = reverse("api:v2:trigger_bot")
    with mock_llm(["Hello from the bot"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200
    assert response.json()["channel"] == {"platform": ChannelPlatform.EMAIL, "data": {}}


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_trigger_bot_ignores_connect_metadata_on_non_connect_platform(experiment, django_capture_on_commit_callbacks):
    """A stray ``commcare_connect_channel_id`` on a non-Connect participant must not leak into ``data``."""
    identifier = "user@example.com"
    participant = ParticipantFactory.create(team=experiment.team, identifier=identifier, platform=ChannelPlatform.EMAIL)
    ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata={"commcare_connect_channel_id": "should-not-leak"},
    )
    ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
    )

    api_user = experiment.team.members.first()
    client = ApiTestClient(api_user, experiment.team)
    data = {
        "identifier": identifier,
        "platform": ChannelPlatform.EMAIL,
        "experiment": str(experiment.public_id),
        "prompt_text": "Say hello",
    }
    url = reverse("api:v2:trigger_bot")
    with mock_llm(["Hello"]):
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, json.dumps(data), content_type="application/json")

    assert response.status_code == 200
    assert response.json()["channel"] == {"platform": ChannelPlatform.EMAIL, "data": {}}

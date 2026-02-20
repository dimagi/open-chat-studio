"""Tests verifying that webhook views set team and experiment context on the request."""

import json

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

from .test_connect_integration import _build_user_message, _setup_participant

PATCH_SET_CURRENT_TEAM = "apps.channels.views.set_current_team"


@pytest.fixture()
def twilio_provider(db):
    return MessagingProviderFactory(
        name="twilio", type=MessagingProviderType.twilio, config={"auth_token": "123", "account_sid": "123"}
    )


@pytest.fixture()
def turn_io_provider(db):
    return MessagingProviderFactory(name="turnio", type=MessagingProviderType.turnio, config={"auth_token": "123"})


@pytest.mark.django_db()
class TestTelegramViewSetsContext:
    @override_settings(TELEGRAM_SECRET_TOKEN="secret")
    def test_sets_team_and_experiment_on_request(self, client):
        from unittest.mock import patch

        from apps.channels.tasks import handle_telegram_message

        channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM)
        data = {"update_id": 1, "message": {"message_id": 1, "text": "hi"}}

        with patch.object(handle_telegram_message, "delay"):
            response = client.post(
                reverse("channels:new_telegram_message", args=[channel.external_id]),
                json.dumps(data),
                content_type="application/json",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
            )

        assert response.status_code == 200
        assert response.wsgi_request.experiment == channel.experiment


@pytest.mark.django_db()
class TestTwilioViewSetsContext:
    def test_sets_team_and_experiment_on_request(self, client, twilio_provider):
        from unittest.mock import patch

        from apps.channels.tasks import handle_twilio_message

        channel = ExperimentChannelFactory(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=twilio_provider,
            experiment__team=twilio_provider.team,
            extra_data={"number": "+14155238886"},
        )
        post_data = {
            "Body": "Hello",
            "From": "whatsapp:+27123456789",
            "To": "whatsapp:+14155238886",
            "MessageSid": "SM123",
            "AccountSid": "123",
            "NumMedia": "0",
        }

        with (
            patch.object(handle_twilio_message, "delay"),
            patch("apps.channels.views.tasks.validate_twillio_request", return_value=True),
        ):
            response = client.post(reverse("channels:new_twilio_message"), post_data)

        assert response.status_code == 200
        assert response.wsgi_request.experiment == channel.experiment


@pytest.mark.django_db()
class TestSureAdhereViewSetsContext:
    def test_sets_team_and_experiment_on_request(self, client):
        from unittest.mock import patch

        from apps.channels.tasks import handle_sureadhere_message

        channel = ExperimentChannelFactory(
            platform=ChannelPlatform.SUREADHERE,
            extra_data={"sureadhere_tenant_id": "42"},
        )
        data = {"patientId": "6225", "type": "InboundSMS", "body": "Hi", "from": "+27123"}

        with patch.object(handle_sureadhere_message, "delay"):
            response = client.post(
                reverse("channels:new_sureadhere_message", args=["42"]),
                json.dumps(data),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.wsgi_request.experiment == channel.experiment


@pytest.mark.django_db()
class TestTurnViewSetsContext:
    def test_sets_team_and_experiment_on_request(self, client, turn_io_provider):
        from unittest.mock import patch

        from apps.channels.tasks import handle_turn_message

        channel = ExperimentChannelFactory(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=turn_io_provider,
            experiment__team=turn_io_provider.team,
        )
        data = {"messages": [{"id": "1", "from": "+27123", "text": {"body": "hi"}, "type": "text"}]}

        with patch.object(handle_turn_message, "delay"):
            response = client.post(
                reverse("channels:new_turn_message", args=[channel.experiment.public_id]),
                json.dumps(data),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.wsgi_request.experiment == channel.experiment


@pytest.mark.django_db()
class TestConnectViewSetsContext:
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_sets_team_and_experiment_on_request(self, client, experiment):
        import base64
        import hashlib
        import hmac
        from unittest.mock import patch

        from apps.channels.tasks import handle_commcare_connect_message

        commcare_connect_channel_id, encryption_key, channel, _ = _setup_participant(experiment)
        payload = _build_user_message(encryption_key, commcare_connect_channel_id)
        body = json.dumps(payload).encode("utf-8")
        key = settings.COMMCARE_CONNECT_SERVER_SECRET.encode()
        digest = hmac.new(key=key, msg=body, digestmod=hashlib.sha256).digest()

        with patch.object(handle_commcare_connect_message, "delay"):
            response = client.post(
                reverse("channels:new_connect_message"),
                body,
                content_type="application/json",
                headers={"X-MAC-DIGEST": base64.b64encode(digest)},
            )

        assert response.status_code == 200
        assert response.wsgi_request.experiment == channel.experiment

import json

from django.test import TestCase
from django.urls import reverse
from mock import patch

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.tasks import handle_facebook_message
from apps.chat.channels import MESSAGE_TYPES
from apps.experiments.models import ConsentForm, Experiment, Prompt
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team
from apps.users.models import CustomUser


class FacebookChannelTest(TestCase):
    def setUp(self):
        super().setUp()
        self.page_id = "12345"
        self.page_access_token = "678910"
        self.team = Team.objects.create(name="test-team", slug="test-team")
        self.user = CustomUser.objects.create_user(username="testuser")
        self.prompt = Prompt.objects.create(
            team=self.team,
            owner=self.user,
            name="test-prompt",
            description="test",
            prompt="You are a helpful assistant",
        )
        self.experiment = Experiment.objects.create(
            team=self.team,
            owner=self.user,
            name="TestExperiment",
            description="test",
            chatbot_prompt=self.prompt,
            consent_form=ConsentForm.get_default(self.team),
            llm_provider=LlmProvider.objects.create(
                name="test",
                type="openai",
                team=self.team,
                config={
                    "openai_api_key": "123123123",
                },
            ),
        )
        self.facebook_details = {
            "page_id": self.page_id,
            "page_access_token": self.page_access_token,
            "verify_token": "123456789",
        }
        self.experiment_channel = ExperimentChannel.objects.create(
            name="TestChannel",
            experiment=self.experiment,
            extra_data=self.facebook_details,
            platform=ChannelPlatform.FACEBOOK,
        )

    def test_facebook_get_request_success(self):
        """Tests Facebook's get request that verifies the server"""
        url = reverse("channels:new_facebook_message", kwargs={"team_slug": self.team.slug})
        verify_token = self.facebook_details["verify_token"]
        query_string = f"?hub.mode=subscribe&hub.challenge=123456789&hub.verify_token={verify_token}"
        response = self.client.get(f"{url}{query_string}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, verify_token.encode("utf-8"))

    def test_facebook_get_request_failes(self):
        """Tests Facebook's get request that verifies the server"""
        url = reverse("channels:new_facebook_message", kwargs={"team_slug": self.team.slug})
        query_string = "?hub.mode=subscribe&hub.challenge=123456789&hub.verify_token=rubbish"
        response = self.client.get(f"{url}{query_string}")
        self.assertEqual(response.status_code, 403)

    @patch("apps.channels.tasks.FacebookMessengerChannel.new_user_message")
    def test_incoming_text_message(self, new_user_message):
        """Verify that a FacebookMessage object is being built correctly for text messages"""
        message = _facebook_text_message(self.page_id, message="Hi there")
        handle_facebook_message(team_slug=self.team.slug, message_data=message)
        called_args, called_kwargs = new_user_message.call_args
        facebook_message = called_args[0]
        self.assertEqual(facebook_message.page_id, self.page_id)
        self.assertEqual(facebook_message.message_text, "Hi there")
        self.assertEqual(facebook_message.content_type, MESSAGE_TYPES.TEXT)
        self.assertEqual(facebook_message.user_id, "6785984231")
        self.assertEqual(facebook_message.media_url, None)

    @patch("apps.channels.tasks.FacebookMessengerChannel.new_user_message")
    def test_incoming_voice_message(self, new_user_message):
        """Verify that a FacebookMessage object is being built correctly for voice messages"""
        media_url = "https://example.com/my-audio"
        message = _facebook_audio_message(self.page_id, attachment_url=media_url)
        handle_facebook_message(team_slug=self.team.slug, message_data=message)
        called_args, called_kwargs = new_user_message.call_args
        facebook_message = called_args[0]
        self.assertEqual(facebook_message.page_id, self.page_id)
        self.assertEqual(facebook_message.message_text, "")
        self.assertEqual(facebook_message.content_type, MESSAGE_TYPES.VOICE)
        self.assertEqual(facebook_message.user_id, "6785984231")
        self.assertEqual(facebook_message.media_url, media_url)


def _facebook_text_message(page_id: str, message: str):
    data = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1699259350301,
                "messaging": [
                    {
                        "sender": {"id": "6785984231"},
                        "recipient": {"id": page_id},
                        "timestamp": 1699259349974,
                        "message": {
                            "mid": "m_IAx--vsBAYF3FYqN0LQN3sU3K_suxsIcKASSDHASDSLCbwvO5IBJmx5wFIAvBhWtttttttttt2dOteWfSYYI59BlctQ",
                            "text": message,
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(data)


def _facebook_audio_message(page_id: str, attachment_url: str):
    data = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1699260776574,
                "messaging": [
                    {
                        "sender": {"id": "6785984231"},
                        "recipient": {
                            "id": page_id,
                        },
                        "timestamp": 1699259349974,
                        "message": {
                            "mid": "m_IAx--vsBAYF3FYqN0LQN3sU3K_suxsIcKASSDHASDSLCbwvO5IBJmx5wFIAvBhWtttttttttt2dOteWfSYYI59BlctQ",
                            "attachments": [{"type": "audio", "payload": {"url": attachment_url}}],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(data)

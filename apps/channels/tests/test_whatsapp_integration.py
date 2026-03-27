from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.channels.datamodels import MetaCloudAPIMessage, TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES, WhatsappChannel
from apps.chat.models import Chat, ChatMessage
from apps.files.models import File
from apps.service_providers.models import MessagingProviderType
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

from .message_examples import meta_cloud_api_messages, turnio_messages, twilio_messages


@pytest.fixture()
def turnio_whatsapp_channel(turn_io_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=turn_io_provider,
        experiment__team=turn_io_provider.team,
        extra_data={"number": "+14155238886"},
    )


@pytest.fixture()
def meta_cloud_api_whatsapp_channel(meta_cloud_api_provider):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=meta_cloud_api_provider,
        experiment__team=meta_cloud_api_provider.team,
        extra_data={"number": "+15551234567", "phone_number_id": "12345"},
    )


@pytest.fixture()
def _twilio_whatsapp_channel(twilio_provider):
    ExperimentChannelFactory.create(
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=twilio_provider,
        experiment__team=twilio_provider.team,
        extra_data={"number": "+14155238886"},
    )


class TestTwilio:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [(twilio_messages.Whatsapp.text_message(), "text"), (twilio_messages.Whatsapp.audio_message(), "voice")],
    )
    def test_parse_messages(self, message, message_type):
        whatsapp_message = TwilioMessage.parse(message)
        assert whatsapp_message.platform == ChannelPlatform.WHATSAPP
        assert whatsapp_message.participant_id == "+27456897512"
        if message_type == "text":
            assert whatsapp_message.content_type == MESSAGE_TYPES.TEXT
            assert whatsapp_message.media_url is None
        else:
            assert whatsapp_message.content_type == MESSAGE_TYPES.VOICE
            assert whatsapp_message.media_url == "http://example.com/media"

    @pytest.mark.usefixtures("_twilio_whatsapp_channel")
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [(twilio_messages.Whatsapp.text_message(), "text"), (twilio_messages.Whatsapp.audio_message(), "audio")],
    )
    @override_settings(WHATSAPP_S3_AUDIO_BUCKET="123")
    @patch("apps.channels.tasks.validate_twillio_request", Mock())
    @patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TwilioService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_twilio_uses_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        get_voice_transcript_mock,
        synthesize_voice_mock,
        incoming_message,
        message_type,
    ):
        """Test that the twilio integration can use the WhatsappChannel implementation"""
        synthesize_voice_mock.return_value = SynthesizedAudio(audio=BytesIO(b"123"), duration=10, format="mp3")
        with (
            patch("apps.service_providers.messaging_service.TwilioService.s3_client"),
            patch("apps.service_providers.messaging_service.TwilioService.client"),
        ):
            experiment = ExperimentFactory.create(conversational_consent_enabled=True)
            chat = Chat.objects.create(team=experiment.team)
            bot_process_input.return_value = ChatMessage.objects.create(content="Hi", chat=chat)
            get_voice_transcript_mock.return_value = "Hi"

            handle_twilio_message(message_data=incoming_message)

            if message_type == "text":
                send_text_message.assert_called()
            elif message_type == "audio":
                send_voice_message.assert_called()

    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.TwilioService.client")
    def test_attachments_are_sent_as_separate_messages(self, twilio_client_mock, experiment, twilio_provider):
        """
        Test that the bot's response is sent along with a message for each supported attachment
        """
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP, messaging_provider=twilio_provider, extra_data={"number": "123"}
        )
        session = ExperimentSessionFactory.create(experiment_channel=channel, experiment=experiment)
        channel = WhatsappChannel.from_experiment_session(session)
        file1 = FileFactory.create(name="f1", content_type="image/jpeg")
        file2 = FileFactory.create(name="f2", content_type="image/jpeg")
        # Ensure files have a non-zero content_size so can_send_file() considers them sendable
        File.objects.filter(pk__in=[file1.pk, file2.pk]).update(content_size=1024)
        file1.refresh_from_db()
        file2.refresh_from_db()

        channel.send_message_to_user("Hi there", [file1, file2])
        message_call = twilio_client_mock.messages.create.mock_calls[0]
        attachment_call_1 = twilio_client_mock.messages.create.mock_calls[1]
        attachment_call_2 = twilio_client_mock.messages.create.mock_calls[2]

        assert message_call.kwargs["body"] == "Hi there"
        assert attachment_call_1.kwargs["body"] == file1.name
        assert attachment_call_1.kwargs["media_url"] == file1.download_link(session.id)

        assert attachment_call_2.kwargs["body"] == file2.name
        assert attachment_call_2.kwargs["media_url"] == file2.download_link(session.id)


class TestTurnio:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [
            (turnio_messages.text_message(), "text"),
            (turnio_messages.voice_message(), "voice"),
        ],
    )
    def test_parse_text_message(self, message, message_type):
        message = TurnWhatsappMessage.parse(message)
        assert message.participant_id == "27456897512"
        if message_type == "text":
            assert message.message_text == "Hi there!"
            assert message.content_type == MESSAGE_TYPES.TEXT
        else:
            assert message.media_id == "180e1c3f-ae50-481b-a9f0-7c698233965f"
            assert message.content_type == MESSAGE_TYPES.VOICE

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [(turnio_messages.text_message(), "text"), (turnio_messages.voice_message(), "audio")],
    )
    @override_settings(WHATSAPP_S3_AUDIO_BUCKET="123")
    @patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_turnio_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        get_voice_transcript_mock,
        synthesize_voice_mock,
        incoming_message,
        message_type,
        turnio_whatsapp_channel,
    ):
        """Test that the turnio integration can use the WhatsappChannel implementation"""
        synthesize_voice_mock.return_value = SynthesizedAudio(audio=BytesIO(b"123"), duration=10, format="mp3")
        experiment = ExperimentFactory.create(conversational_consent_enabled=True)
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Hi", chat=chat)
        get_voice_transcript_mock.return_value = "Hi"
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        if message_type == "text":
            send_text_message.assert_called()
        elif message_type == "audio":
            send_voice_message.assert_called()

    @patch("apps.chat.channels.ChannelBase._handle_supported_message")
    @patch("apps.chat.channels.ChannelBase._handle_unsupported_message")
    def test_unsupported_message_type_does_nothing(
        self, _handle_unsupported_message, _handle_supported_message, db, turnio_whatsapp_channel
    ):
        """Test that unsupported messages are not"""
        incoming_message = turnio_messages.text_message()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        _handle_unsupported_message.assert_called()
        _handle_supported_message.assert_not_called()

    @pytest.mark.django_db()
    @pytest.mark.parametrize("message", [turnio_messages.outbound_message(), turnio_messages.status_message()])
    @patch("apps.channels.tasks.handle_turn_message")
    def test_outbound_and_status_messages_ignored(self, handle_turn_message_task, message, client):
        messaging_provider = MessagingProviderFactory.create(type=MessagingProviderType.turnio)
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP, messaging_provider=messaging_provider
        )
        url = reverse("channels:new_turn_message", kwargs={"experiment_id": channel.experiment.public_id})
        response = client.post(url, data=message, content_type="application/json")
        assert response.status_code == 200
        handle_turn_message_task.assert_not_called()

    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.TurnIOService.client")
    def test_attachment_links_attached_to_message(self, turnio_client, turnio_whatsapp_channel, experiment):
        session = ExperimentSessionFactory.create(experiment_channel=turnio_whatsapp_channel, experiment=experiment)
        channel = WhatsappChannel.from_experiment_session(session)
        files = FileFactory.create_batch(2)
        channel.send_message_to_user("Hi there", files=files)
        call_args = turnio_client.messages.send_text.mock_calls[0].args
        final_message = call_args[1]

        expected_final_message = f"""Hi there

{files[0].name}
{files[0].download_link(session.id)}

{files[1].name}
{files[1].download_link(session.id)}
"""
        assert final_message == expected_final_message


class TestMetaCloudApi:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [
            (meta_cloud_api_messages.text_message_value(), "text"),
            (meta_cloud_api_messages.audio_message_value(), "audio"),
        ],
    )
    def test_parse_messages(self, message, message_type):
        parsed = MetaCloudAPIMessage.parse(message)
        assert parsed.participant_id == "27456897512"
        if message_type == "text":
            assert parsed.message_text == "Hello"
            assert parsed.content_type == MESSAGE_TYPES.TEXT
            assert parsed.whatsapp_message_id == "wamid.abc123"
        else:
            assert parsed.media_id == "1215194677037265"
            assert parsed.content_type == MESSAGE_TYPES.VOICE
            assert parsed.whatsapp_message_id == "wamid.abc456"

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [
            (meta_cloud_api_messages.text_message_value(), "text"),
            (meta_cloud_api_messages.audio_message_value(), "audio"),
        ],
    )
    @override_settings(WHATSAPP_S3_AUDIO_BUCKET="123")
    @patch("apps.service_providers.speech_service.SpeechService.synthesize_voice")
    @patch("apps.chat.channels.ChannelBase._get_voice_transcript")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_voice_message")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_meta_cloud_api_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        get_voice_transcript_mock,
        synthesize_voice_mock,
        incoming_message,
        message_type,
        meta_cloud_api_whatsapp_channel,
    ):
        """Test that the Meta Cloud API integration can use the WhatsappChannel implementation"""
        synthesize_voice_mock.return_value = SynthesizedAudio(audio=BytesIO(b"123"), duration=10, format="mp3")
        experiment = ExperimentFactory.create(conversational_consent_enabled=True)
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Hi", chat=chat)
        get_voice_transcript_mock.return_value = "Hi"
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=incoming_message,
        )
        if message_type == "text":
            send_text_message.assert_called()
        elif message_type == "audio":
            send_voice_message.assert_called()

    @patch("apps.chat.channels.ChannelBase._handle_supported_message")
    @patch("apps.chat.channels.ChannelBase._handle_unsupported_message")
    def test_unsupported_message_type_does_nothing(
        self, _handle_unsupported_message, _handle_supported_message, db, meta_cloud_api_whatsapp_channel
    ):
        incoming_message = meta_cloud_api_messages.text_message_value()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=incoming_message,
        )
        _handle_unsupported_message.assert_called()
        _handle_supported_message.assert_not_called()

    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_typing_indicator")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_typing_indicator_sent_on_submit_input_to_llm(
        self,
        bot_process_input,
        send_text_message,
        send_typing_indicator,
        meta_cloud_api_whatsapp_channel,
    ):
        """Test that a typing indicator is sent when the user message is submitted to the LLM."""
        experiment = ExperimentFactory.create(conversational_consent_enabled=True)
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Hi", chat=chat)

        incoming_message = meta_cloud_api_messages.text_message_value()
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=incoming_message,
        )

        send_typing_indicator.assert_called_once_with(
            from_="12345",
            message_id="wamid.abc123",
        )

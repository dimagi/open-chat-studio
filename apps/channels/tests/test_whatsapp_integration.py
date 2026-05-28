from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.channels.channels_v2.stages.core import WhatsappAttachmentHydrationStage
from apps.channels.channels_v2.whatsapp_channel import WhatsappChannel
from apps.channels.datamodels import TwilioMessage, WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.channels.tests.channels.conftest import make_context
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat, ChatMessage
from apps.files.models import File, FilePurpose
from apps.service_providers.file_limits import WHATSAPP_INBOUND_MAX_BYTES
from apps.service_providers.messaging_service import MetaCloudAPIService, TurnIOService, TwilioService
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
    @patch("apps.channels.channels_v2.stages.core.QueryExtractionStage._transcribe_voice")
    @patch("apps.service_providers.messaging_service.TwilioService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_twilio_uses_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        transcribe_voice_mock,
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
            transcribe_voice_mock.return_value = "Hi"

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
        channel = WhatsappChannel(session.experiment, session.experiment_channel, session)
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
        message = WhatsAppMessage.parse(message)
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
    @patch("apps.channels.channels_v2.stages.core.QueryExtractionStage._transcribe_voice")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_voice_message")
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_turnio_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        transcribe_voice_mock,
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
        transcribe_voice_mock.return_value = "Hi"
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        if message_type == "text":
            send_text_message.assert_called()
        elif message_type == "audio":
            send_voice_message.assert_called()

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_unsupported_message_type_does_nothing(self, bot_process_input, db, turnio_whatsapp_channel):
        """Test that unsupported messages are not processed by the bot"""
        incoming_message = turnio_messages.text_message()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_turn_message(experiment_id=turnio_whatsapp_channel.experiment.public_id, message_data=incoming_message)
        bot_process_input.assert_not_called()

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
        channel = WhatsappChannel(session.experiment, session.experiment_channel, session)
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
        parsed = WhatsAppMessage.parse(message)
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
    @patch("apps.channels.channels_v2.stages.core.QueryExtractionStage._transcribe_voice")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_voice_message")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_meta_cloud_api_whatsapp_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        send_voice_message,
        transcribe_voice_mock,
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
        transcribe_voice_mock.return_value = "Hi"
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=incoming_message,
        )
        if message_type == "text":
            send_text_message.assert_called()
        elif message_type == "audio":
            send_voice_message.assert_called()

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_unsupported_message_type_does_nothing(self, bot_process_input, db, meta_cloud_api_whatsapp_channel):
        incoming_message = meta_cloud_api_messages.text_message_value()
        incoming_message["messages"][0]["type"] = "video"
        incoming_message["messages"][0]["video"] = {}
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=incoming_message,
        )
        bot_process_input.assert_not_called()

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


# ---------------------------------------------------------------------------
# Parser tests — image messages are classified as TEXT
# ---------------------------------------------------------------------------


class TestTwilioImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """Twilio image MMS should be classified as TEXT so it passes MessageTypeValidationStage."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_preserves_media_url(self):
        """Twilio image MMS should preserve the media URL for download."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.media_url == "http://example.com/media"

    def test_parse_image_message_preserves_body_text(self):
        """Twilio image messages carry the text body (MMS caption) unchanged."""
        raw = twilio_messages.Whatsapp.image_message()
        raw["Body"] = "MMS caption"
        parsed = TwilioMessage.parse(raw)
        assert parsed.message_text == "MMS caption"

    def test_parse_image_attachment_mime_type_preserved(self):
        """attachment_mime_type should hold the original MIME string."""
        parsed = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        assert parsed.attachment_mime_type == "image/png"


class TestTurnioImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """TurnIO image message should be classified as TEXT."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_uses_caption_as_body(self):
        """TurnIO image caption becomes message_text."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message(caption="Look at this!"))
        assert parsed.message_text == "Look at this!"

    def test_parse_image_message_empty_caption_gives_empty_body(self):
        """An image with no caption should produce empty message_text."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message(caption=""))
        assert parsed.message_text == ""

    def test_parse_image_message_preserves_media_id(self):
        """TurnIO image media_id is required for download."""
        parsed = WhatsAppMessage.parse(turnio_messages.image_message())
        assert parsed.media_id == "turn-image-media-id-789"


class TestMetaCloudApiImageParsing:
    def test_parse_image_message_content_type_is_text(self):
        """Meta Cloud API image message should be classified as TEXT."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.content_type == MESSAGE_TYPES.TEXT

    def test_parse_image_message_uses_caption_as_body(self):
        """Meta image caption becomes message_text."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value(caption="Check this out"))
        assert parsed.message_text == "Check this out"

    def test_parse_image_message_empty_caption_gives_empty_body(self):
        """An image with no caption should produce empty message_text."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value_no_caption())
        assert parsed.message_text == ""

    def test_parse_image_message_preserves_media_id(self):
        """Meta image media_id is required to download via Media API."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.media_id == "image-media-id-456"

    def test_parse_image_message_preserves_whatsapp_message_id(self):
        """whatsapp_message_id should still be captured for the typing indicator."""
        parsed = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        assert parsed.whatsapp_message_id == "wamid.img001"


# ---------------------------------------------------------------------------
# download_message_media() — provider-level media fetch
# ---------------------------------------------------------------------------


class TestTwilioDownloadMedia:
    def test_download_image_returns_bytes_and_content_type(self):
        """TwilioService.download_message_media() returns (bytes, content_type) for an image."""
        service = TwilioService(account_sid="SID", auth_token="TOKEN")
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())

        mock_response = MagicMock()
        mock_response.content = b"\x89PNG"
        mock_response.headers = {"Content-Type": "image/png"}

        with patch("httpx.get", return_value=mock_response) as mock_get:
            data, content_type = service.download_message_media(message)

        mock_get.assert_called_once_with(
            "http://example.com/media",
            auth=("SID", "TOKEN"),
            follow_redirects=True,
        )
        assert data == b"\x89PNG"
        assert content_type == "image/png"


class TestTurnioDownloadMedia:
    @patch("apps.service_providers.messaging_service.TurnIOService.client")
    def test_download_image_returns_bytes_and_content_type(self, mock_client):
        """TurnIOService.download_message_media() returns (bytes, content_type) for an image."""
        service = TurnIOService(auth_token="TOKEN")
        message = WhatsAppMessage.parse(turnio_messages.image_message())

        mock_response = MagicMock()
        mock_response.content = b"\xff\xd8"
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_client.media.get_media.return_value = mock_response

        data, content_type = service.download_message_media(message)

        mock_client.media.get_media.assert_called_once_with("turn-image-media-id-789")
        assert data == b"\xff\xd8"
        assert content_type == "image/jpeg"


class TestMetaCloudApiDownloadMedia:
    def test_download_image_returns_bytes_and_content_type(self):
        """MetaCloudAPIService.download_message_media() resolves media URL then fetches bytes."""
        service = MetaCloudAPIService(
            business_id="BIZ",
            access_token="TOKEN",
        )
        message = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())

        url_response = MagicMock()
        url_response.json.return_value = {"url": "https://cdn.meta.example.com/image123"}
        url_response.raise_for_status = MagicMock()

        media_response = MagicMock()
        media_response.content = b"\x89PNG"
        media_response.headers = {"Content-Type": "image/png"}
        media_response.raise_for_status = MagicMock()

        with patch("httpx.get", side_effect=[url_response, media_response]):
            data, content_type = service.download_message_media(message)

        assert data == b"\x89PNG"
        assert content_type == "image/png"


# ---------------------------------------------------------------------------
# WhatsappAttachmentHydrationStage.process() — File creation per provider
# ---------------------------------------------------------------------------


def _make_stage_context(message, provider, get_inbound_image_return):
    """Build a real experiment/session context with a mocked messaging service.

    Returns (ctx, mock_service) so callers can also assert on service interactions.
    """
    experiment = ExperimentFactory(team=provider.team)
    session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
    mock_service = MagicMock()
    mock_service.get_inbound_image.return_value = get_inbound_image_return
    ctx = make_context(message=message, experiment=experiment, experiment_session=session)
    ctx.experiment_channel.messaging_provider.get_messaging_service.return_value = mock_service
    return ctx, mock_service


class TestWhatsappAttachmentHydrationStageProcess:
    def setup_method(self):
        self.stage = WhatsappAttachmentHydrationStage()

    @pytest.mark.django_db()
    def test_twilio_image_creates_file_record(self, twilio_provider):
        """Twilio image message: one MESSAGE_MEDIA File persisted and attached."""
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        ctx, _ = _make_stage_context(message, twilio_provider, (b"\x89PNG\r\n\x1a\n", "image/png"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA
        assert file_obj.content_type == "image/png"

    @pytest.mark.django_db()
    def test_turnio_image_creates_file_record(self, turn_io_provider):
        """TurnIO image message: one MESSAGE_MEDIA File persisted and attached."""
        message = WhatsAppMessage.parse(turnio_messages.image_message())
        ctx, _ = _make_stage_context(message, turn_io_provider, (b"\xff\xd8\xff\xe0", "image/jpeg"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA

    @pytest.mark.django_db()
    def test_meta_image_creates_file_record(self, meta_cloud_api_provider):
        """Meta image message: one MESSAGE_MEDIA File persisted and attached."""
        message = WhatsAppMessage.parse(meta_cloud_api_messages.image_message_value())
        ctx, _ = _make_stage_context(message, meta_cloud_api_provider, (b"\x89PNG\r\n\x1a\n", "image/png"))

        self.stage.process(ctx)

        assert len(message.attachments) == 1
        file_obj = File.objects.get(id=message.attachments[0].file_id)
        assert file_obj.purpose == FilePurpose.MESSAGE_MEDIA

    @pytest.mark.django_db()
    def test_non_image_message_creates_no_files(self, twilio_provider):
        """Text-only messages (service returns None) create no File rows."""
        message = TwilioMessage.parse(twilio_messages.Whatsapp.text_message())
        ctx, _ = _make_stage_context(message, twilio_provider, None)

        self.stage.process(ctx)

        assert message.attachments == []
        assert File.objects.filter(purpose=FilePurpose.MESSAGE_MEDIA).count() == 0

    @pytest.mark.django_db()
    def test_oversized_image_skipped_and_note_added(self, twilio_provider):
        """Images exceeding the size limit are skipped; a note is appended to message_text."""
        big_payload = b"x" * (WHATSAPP_INBOUND_MAX_BYTES + 1)
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        message.message_text = ""
        ctx, _ = _make_stage_context(message, twilio_provider, (big_payload, "image/png"))

        self.stage.process(ctx)

        assert message.attachments == []
        assert "skipped" in message.message_text.lower()

    @pytest.mark.django_db()
    def test_disallowed_content_type_skipped_and_note_added(self, twilio_provider):
        """Images with disallowed MIME types are skipped; a note is appended to message_text."""
        message = TwilioMessage.parse(twilio_messages.Whatsapp.image_message())
        message.message_text = ""
        ctx, _ = _make_stage_context(message, twilio_provider, (b"data", "image/tiff"))

        self.stage.process(ctx)

        assert message.attachments == []
        assert "skipped" in message.message_text.lower()


# ---------------------------------------------------------------------------
# End-to-end task integration — attachment_file_ids populated on message
# ---------------------------------------------------------------------------


class TestTwilioInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.channels.tasks.validate_twillio_request", Mock())
    @patch("apps.service_providers.messaging_service.TwilioService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TwilioService.download_message_media")
    def test_inbound_image_creates_file_and_populates_attachment_ids(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        twilio_provider,
    ):
        """handle_twilio_message with an image payload: File created and attachment_file_ids set."""
        download_media_mock.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=twilio_provider,
            experiment__team=twilio_provider.team,
            extra_data={"number": "+14155238886"},
        )
        experiment = channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        with patch("apps.service_providers.messaging_service.TwilioService.client"):
            handle_twilio_message(message_data=twilio_messages.Whatsapp.image_message())

        assert File.objects.filter(purpose=FilePurpose.MESSAGE_MEDIA).count() == 1


class TestTurnioInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.TurnIOService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.TurnIOService.download_message_media")
    def test_inbound_image_creates_file_and_populates_attachment_ids(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        turnio_whatsapp_channel,
    ):
        """handle_turn_message with an image payload: File created."""
        download_media_mock.return_value = (b"\xff\xd8\xff\xe0", "image/jpeg")
        experiment = turnio_whatsapp_channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        handle_turn_message(
            experiment_id=turnio_whatsapp_channel.experiment.public_id,
            message_data=turnio_messages.image_message(),
        )

        assert File.objects.filter(purpose=FilePurpose.MESSAGE_MEDIA).count() == 1


class TestMetaCloudApiInboundImageTask:
    @pytest.mark.django_db()
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.download_message_media")
    def test_inbound_image_creates_file_and_populates_attachment_ids(
        self,
        download_media_mock,
        bot_process_input,
        send_text_message,
        meta_cloud_api_whatsapp_channel,
    ):
        """handle_meta_cloud_api_message with an image payload: File created."""
        download_media_mock.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")
        experiment = meta_cloud_api_whatsapp_channel.experiment
        chat = Chat.objects.create(team=experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="Got it", chat=chat)

        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.team.slug,
            message_data=meta_cloud_api_messages.image_message_value(),
        )

        assert File.objects.filter(purpose=FilePurpose.MESSAGE_MEDIA).count() == 1

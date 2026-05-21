from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.channels.channels_v2.whatsapp_channel import WhatsappChannel
from apps.channels.datamodels import TurnWhatsappMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_meta_cloud_api_message, handle_turn_message, handle_twilio_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Participant
from apps.files.models import File
from apps.service_providers.models import MessagingProviderType
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
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
    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("incoming_value", "message_type"),
        [
            (meta_cloud_api_messages.legacy_text_message_value(), "text"),
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
        incoming_value,
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
            team_slug=meta_cloud_api_whatsapp_channel.experiment.team.slug,
            message_data=incoming_value["messages"][0],
        )
        if message_type == "text":
            send_text_message.assert_called()
        elif message_type == "audio":
            send_voice_message.assert_called()

    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_unsupported_message_type_does_nothing(self, bot_process_input, db, meta_cloud_api_whatsapp_channel):
        incoming_value = meta_cloud_api_messages.legacy_text_message_value()
        message = incoming_value["messages"][0]
        message["type"] = "video"
        message["video"] = {}
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.experiment.team.slug,
            message_data=message,
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

        incoming_message = meta_cloud_api_messages.legacy_text_message_value()["messages"][0]
        handle_meta_cloud_api_message(
            channel_id=meta_cloud_api_whatsapp_channel.id,
            team_slug=meta_cloud_api_whatsapp_channel.experiment.team.slug,
            message_data=incoming_message,
        )

        send_typing_indicator.assert_called_once_with(
            from_="12345",
            message_id="wamid.abc123",
        )


BSUID = "US.13491208655302741918"
PHONE = "27456897512"


def _meta_message(*, bsuid: str, phone: str | None, msg_id: str = "wamid.abc", timestamp: str = "1") -> dict:
    """Build a minimal inbound Meta Cloud API text-message payload.

    ``phone=None`` means the user has adopted a username and Meta has hidden their phone
    number — the webhook carries the BSUID only. ``phone="..."`` means the phone is still
    visible (the user has not adopted a username, or is in the business's contact book).
    """
    message: dict = {
        "from_user_id": bsuid,
        "id": msg_id,
        "timestamp": timestamp,
        "text": {"body": "Hi"},
        "type": "text",
    }
    if phone is not None:
        message["from"] = phone
    return message


@pytest.mark.django_db()
class TestWhatsappParticipantResolution:
    """End-to-end participant-resolution scenarios for the WhatsApp BSUID rollout.

    Five lifecycle scenarios are exercised through the Meta Cloud API webhook entry point.
    The Twilio code path resolves through the same ``WhatsappChannel`` logic — its own
    parser is covered by ``TestTwilioMessageParse`` in ``test_datamodels.py``.

    Each scenario inlines the message it delivers; the presence or absence of ``from``
    (the phone number) is visible at a glance via the ``phone=`` argument to
    ``_meta_message``.
    """

    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_typing_indicator")
    @patch("apps.service_providers.messaging_service.MetaCloudAPIService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def _deliver(self, bot_process_input, send_text_message, send_typing_indicator, channel, message):
        chat = Chat.objects.create(team=channel.experiment.team)
        bot_process_input.return_value = ChatMessage.objects.create(content="ok", chat=chat)
        handle_meta_cloud_api_message(
            channel_id=channel.id,
            team_slug=channel.experiment.team.slug,
            message_data=message,
        )

    def _whatsapp_participants(self, team):
        return Participant.objects.filter(team=team, platform=ChannelPlatform.WHATSAPP)

    def test_scenario_1_new_username_adopter_creates_bsuid_keyed_participant(self, meta_cloud_api_whatsapp_channel):
        """A brand-new user who has already adopted a WhatsApp username sends their first
        message. The webhook carries ONLY the BSUID — no phone number.

        Expected: a new Participant is created, keyed by the BSUID.
        """
        message = _meta_message(bsuid=BSUID, phone=None)

        self._deliver(channel=meta_cloud_api_whatsapp_channel, message=message)

        participants = self._whatsapp_participants(meta_cloud_api_whatsapp_channel.experiment.team)
        assert participants.count() == 1
        assert participants.get().identifier == BSUID

    def test_scenario_2_username_adopter_later_reveals_phone_keeps_same_participant(
        self, meta_cloud_api_whatsapp_channel
    ):
        """A username-adopter sends their first message (BSUID only). Later they share their
        phone — via the contact-info button, or because Meta's contact-book / 30-day cache
        starts including it — and subsequent webhooks now carry both BSUID and phone.

        Expected: the second webhook resolves to the SAME participant created at T1. No
        duplicate is created.
        """
        team = meta_cloud_api_whatsapp_channel.experiment.team

        # T1: phone hidden — webhook has BSUID only.
        self._deliver(
            channel=meta_cloud_api_whatsapp_channel,
            message=_meta_message(bsuid=BSUID, phone=None, msg_id="wamid.t1"),
        )

        # T2: phone now visible — webhook has BSUID + phone.
        self._deliver(
            channel=meta_cloud_api_whatsapp_channel,
            message=_meta_message(bsuid=BSUID, phone=PHONE, msg_id="wamid.t2"),
        )

        participants = self._whatsapp_participants(team)
        assert participants.count() == 1
        assert participants.get().identifier == BSUID

    def test_scenario_3_new_user_without_username_creates_bsuid_keyed_participant(
        self, meta_cloud_api_whatsapp_channel
    ):
        """A brand-new user who has NOT adopted a username sends their first message. The
        webhook carries both BSUID and phone.

        Expected: a new Participant is created, keyed by the BSUID — not the phone. From
        this point on, the participant is BSUID-keyed regardless of phone visibility.
        """
        message = _meta_message(bsuid=BSUID, phone=PHONE)

        self._deliver(channel=meta_cloud_api_whatsapp_channel, message=message)

        participants = self._whatsapp_participants(meta_cloud_api_whatsapp_channel.experiment.team)
        assert participants.count() == 1
        assert participants.get().identifier == BSUID

    def test_scenario_4_pre_rollout_phone_keyed_participant_is_matched_via_phone(self, meta_cloud_api_whatsapp_channel):
        """A participant from before the BSUID rollout already exists in the DB, keyed by
        their phone number. After rollout, Meta starts including the BSUID in webhooks.

        Expected: the post-rollout webhook (BSUID + phone) resolves to the existing
        phone-keyed participant via the phone clause. No duplicate is created. The
        identifier on the existing participant is NOT rewritten to the BSUID.
        """
        team = meta_cloud_api_whatsapp_channel.experiment.team
        legacy = ParticipantFactory.create(team=team, identifier=PHONE, platform=ChannelPlatform.WHATSAPP)

        self._deliver(
            channel=meta_cloud_api_whatsapp_channel,
            message=_meta_message(bsuid=BSUID, phone=PHONE),
        )

        participants = self._whatsapp_participants(team)
        assert participants.count() == 1
        assert participants.get().pk == legacy.pk
        legacy.refresh_from_db()
        assert legacy.identifier == PHONE

    def test_scenario_5_pre_rollout_phone_keyed_participant_adopts_username_loses_continuity(
        self, meta_cloud_api_whatsapp_channel
    ):
        """A pre-rollout participant (keyed by phone) adopts a username AFTER the rollout.
        Their webhook now carries only the BSUID — no phone — and Meta's contact book and
        30-day cache do not contain their phone number.

        Expected: we have nothing to match against; a new BSUID-keyed participant is
        created and the legacy phone-keyed participant is untouched. This is the one
        accepted continuity loss in the rollout design.
        """
        team = meta_cloud_api_whatsapp_channel.experiment.team
        legacy = ParticipantFactory.create(team=team, identifier=PHONE, platform=ChannelPlatform.WHATSAPP)

        self._deliver(
            channel=meta_cloud_api_whatsapp_channel,
            message=_meta_message(bsuid=BSUID, phone=None),
        )

        participants = self._whatsapp_participants(team)
        assert set(participants.values_list("identifier", flat=True)) == {PHONE, BSUID}
        legacy.refresh_from_db()
        assert legacy.identifier == PHONE

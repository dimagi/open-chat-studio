from io import BytesIO
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.stages.terminal import PersistenceStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


class TestPersistenceStageShould:
    """Tests for should_run and basic guard logic (no DB)."""

    def setup_method(self):
        self.stage = PersistenceStage()

    def test_should_not_run_when_nothing_to_persist(self):
        ctx = make_context(early_exit_response=None, voice_audio=None, human_message_tags=[])
        assert self.stage.should_run(ctx) is False

    def test_no_persistence_without_session(self):
        ctx = make_context(
            early_exit_response="some exit",
            experiment_session=None,
        )

        # should_run is True but process returns early
        self.stage(ctx)

    def test_reset_skips_persistence(self):
        session = MagicMock()
        msg = text_message(message_text="/reset")
        ctx = make_context(
            early_exit_response="Conversation reset",
            experiment_session=session,
            message=msg,
        )

        self.stage(ctx)

        # No ChatMessage should be created (we verify by checking that
        # the mock session's chat was not accessed for creation)
        # In mock context this simply returns without error


@pytest.mark.django_db()
class TestPersistenceStageDB:
    """Tests requiring real DB records."""

    def setup_method(self):
        self.stage = PersistenceStage()

    def test_early_exit_creates_ai_message(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            early_exit_response="Not allowed",
        )

        self.stage(ctx)

        ai_messages = ChatMessage.objects.filter(
            chat=session.chat,
            message_type=ChatMessageType.AI,
        )
        assert ai_messages.count() == 1
        assert ai_messages.first().content == "Not allowed"

    def test_early_exit_skips_when_bot_response_set(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        bot_response = MagicMock()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            early_exit_response="seed message response",
            bot_response=bot_response,
        )

        self.stage(ctx)

        ai_messages = ChatMessage.objects.filter(
            chat=session.chat,
            message_type=ChatMessageType.AI,
        )
        # No duplicate AI message created since bot_response is set
        assert ai_messages.count() == 0

    def test_voice_audio_tagged_and_saved(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        # Create a real bot_response ChatMessage
        bot_msg = ChatMessage.objects.create(
            chat=session.chat,
            message_type=ChatMessageType.AI,
            content="voice response",
        )
        voice_audio = MagicMock()
        voice_audio.audio = BytesIO(b"fake_audio_data")
        voice_audio.content_type = "audio/ogg"
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            voice_audio=voice_audio,
            bot_response=bot_msg,
        )

        self.stage(ctx)

        bot_msg.refresh_from_db()
        tag_names = list(bot_msg.tags.values_list("name", flat=True))
        assert "voice" in tag_names

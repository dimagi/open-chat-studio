from unittest.mock import MagicMock, patch

import pytest

from apps.channels.channels_v2.stages.core import ChatMessageCreationStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message
from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
class TestChatMessageCreationStage:
    def setup_method(self):
        self.stage = ChatMessageCreationStage()

    def test_should_not_run_without_query(self):
        ctx = make_context(user_query=None)
        assert self.stage.should_run(ctx) is False

    def test_creates_human_message(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        msg = text_message()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="Hello",
        )

        self.stage(ctx)

        assert ctx.human_message is not None
        assert ctx.human_message.message_type == ChatMessageType.HUMAN
        assert ctx.human_message.content == "Hello"

    def test_voice_message_tagged(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        msg = audio_message()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="transcribed text",
        )

        self.stage(ctx)

        assert ctx.human_message is not None
        tag_names = list(ctx.human_message.tags.values_list("name", flat=True))
        assert "voice" in tag_names

    @patch("apps.channels.channels_v2.stages.core.enqueue_static_triggers")
    def test_static_triggers_fired(self, mock_enqueue):
        mock_enqueue.delay = MagicMock()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        capabilities = make_capabilities(supports_static_triggers=True)
        msg = text_message()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="Hello",
            capabilities=capabilities,
        )

        self.stage(ctx)

        mock_enqueue.delay.assert_called_once()

    @patch("apps.channels.channels_v2.stages.core.enqueue_static_triggers")
    def test_static_triggers_not_fired_when_disabled(self, mock_enqueue):
        mock_enqueue.delay = MagicMock()
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        capabilities = make_capabilities(supports_static_triggers=False)
        msg = text_message()
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="Hello",
            capabilities=capabilities,
        )

        self.stage(ctx)

        mock_enqueue.delay.assert_not_called()

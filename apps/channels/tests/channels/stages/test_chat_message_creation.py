from unittest.mock import MagicMock, patch

import pytest

from apps.channels.datamodels import Attachment, BaseMessage
from apps.channels.stages.core import ChatMessageCreationStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.channels.tests.message_examples.base_messages import audio_message, text_message
from apps.chat.models import ChatMessageType
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER


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

    @pytest.mark.parametrize(
        "user_query",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="spaces"),
            pytest.param("\n\n", id="newlines"),
        ],
    )
    def test_empty_query_persists_placeholder(self, user_query):
        """An empty message must be stored as the placeholder the LLM is given.

        Persisting the raw empty string makes every later turn in the session replay an
        empty text content block, which Anthropic rejects with a 400.
        """
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=text_message(message_text=user_query),
            user_query=user_query,
        )

        self.stage(ctx)

        assert ctx.human_message.content == EMPTY_MESSAGE_PLACEHOLDER
        assert ctx.user_query == EMPTY_MESSAGE_PLACEHOLDER

    def test_empty_query_with_attachment_is_left_empty(self):
        """Attachment-only messages carry their content in the attachments, not the text."""
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        msg = text_message(message_text="")
        msg.attachments = [
            Attachment(
                file_id=1,
                type="code_interpreter",
                name="thing.png",
                size=10,
                content_type="image/png",
                download_link="http://example.com/thing.png",
            )
        ]
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="",
        )

        self.stage(ctx)

        assert ctx.human_message.content == ""
        assert ctx.user_query == ""

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

    @patch("apps.channels.stages.core.enqueue_static_triggers")
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

    @patch("apps.channels.stages.core.enqueue_static_triggers")
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

    def test_persists_external_ids(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        msg = BaseMessage(
            participant_id="123",
            message_text="Hello",
            external_ids=["connect:aaa", "connect:bbb"],
        )
        ctx = make_context(
            experiment=experiment,
            experiment_session=session,
            message=msg,
            user_query="Hello",
        )

        self.stage(ctx)

        ctx.human_message.refresh_from_db()
        assert ctx.human_message.external_ids == ["connect:aaa", "connect:bbb"]

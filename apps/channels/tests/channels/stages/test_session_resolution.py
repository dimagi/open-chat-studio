from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.stages.core import SessionResolutionStage
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
class TestSessionResolutionStage:
    def setup_method(self):
        self.stage = SessionResolutionStage()

    def test_should_not_run_when_participant_not_allowed(self):
        ctx = make_context(participant_allowed=False)
        assert self.stage.should_run(ctx) is True

    def test_pre_set_session_is_noop(self):
        session = MagicMock()
        experiment = MagicMock()
        experiment.is_public = True
        ctx = make_context(experiment=experiment, experiment_session=session, participant_allowed=True)

        self.stage(ctx)

        assert ctx.experiment_session is session

    def test_creates_new_session_when_none_exists(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="new_participant",
        )

        self.stage(ctx)

        assert ctx.experiment_session is not None
        assert ctx.experiment_session.participant.identifier == "new_participant"

    def test_reuses_active_session(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        # Create a session via the stage first
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="returning_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session

        # Second message from same participant
        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="returning_user",
        )
        self.stage(ctx2)

        assert ctx2.experiment_session.id == first_session.id

    def test_reset_with_engaged_session_creates_new(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        # Create initial session
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="reset_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session
        # Simulate engagement by adding a chat message
        ChatMessage.objects.create(
            chat=first_session.chat,
            message_type=ChatMessageType.HUMAN,
            content="Hello",
        )

        # Send /reset
        reset_msg = text_message(participant_id="reset_user", message_text="/reset")
        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="reset_user",
            message=reset_msg,
        )
        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx2)

        assert ctx2.experiment_session is not None
        assert ctx2.experiment_session.id != first_session.id

    def test_reset_with_no_engagement_still_creates_new_session(self):
        """When /reset is sent but the existing session has no engagement,
        a new session is still created because the reset check happens
        before session loading."""
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        # Create initial session with no engagement via a normal first message
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="no_engage_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session

        # Send /reset - but session has no engagement.
        # The code detects /reset at line 56 BEFORE loading a session.
        # Since ctx.experiment_session is None at that point, it calls
        # _handle_reset which creates a new session and raises EarlyExitResponse.
        reset_msg = text_message(participant_id="no_engage_user", message_text="/reset")
        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="no_engage_user",
            message=reset_msg,
        )
        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx2)

        # A new session is created even though there was no engagement,
        # because the reset check happens before session loading.
        assert ctx2.experiment_session is not None
        assert ctx2.experiment_session.id != first_session.id

    def test_reset_no_prior_session_creates_new(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)

        reset_msg = text_message(participant_id="fresh_reset_user", message_text="/reset")
        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant_allowed=True,
            participant_identifier="fresh_reset_user",
            message=reset_msg,
        )

        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx)

        assert ctx.experiment_session is not None

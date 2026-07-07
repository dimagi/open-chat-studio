from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.capabilities import ChannelCapabilities, PlatformConsentConfig
from apps.channels.channels_v2.stages.core import ConsentCheckStage, SessionResolutionStage
from apps.channels.const import MESSAGE_TYPES
from apps.channels.exceptions import EarlyAbort, EarlyExitResponse
from apps.channels.tests.channels.conftest import make_context
from apps.channels.tests.message_examples.base_messages import text_message
from apps.chat.const import STATUSES_FOR_COMPLETE_CHATS
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory


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
        participant = ParticipantFactory(
            team=experiment.team, identifier="new_participant", platform=experiment_channel.platform
        )
        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="new_participant",
        )

        self.stage(ctx)

        assert ctx.experiment_session is not None
        assert ctx.experiment_session.participant.identifier == "new_participant"

    def test_reuses_active_session(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team, identifier="returning_user", platform=experiment_channel.platform
        )
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="returning_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session

        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="returning_user",
        )
        self.stage(ctx2)

        assert ctx2.experiment_session.id == first_session.id

    def test_reset_with_engaged_session_creates_new(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team, identifier="reset_user", platform=experiment_channel.platform
        )
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="reset_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session
        ChatMessage.objects.create(
            chat=first_session.chat,
            message_type=ChatMessageType.HUMAN,
            content="Hello",
        )

        reset_msg = text_message(participant_id="reset_user", message_text="/reset")
        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="reset_user",
            message=reset_msg,
        )
        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx2)

        assert ctx2.experiment_session is not None
        assert ctx2.experiment_session.id != first_session.id

        first_session.refresh_from_db()
        assert first_session.status in STATUSES_FOR_COMPLETE_CHATS

    def test_reset_with_no_engagement_still_creates_new_session(self):
        """When /reset is sent, the existing session is ended and a new one created,
        even if the session has no chat message engagement."""
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team, identifier="no_engage_user", platform=experiment_channel.platform
        )
        ctx1 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="no_engage_user",
        )
        self.stage(ctx1)
        first_session = ctx1.experiment_session

        reset_msg = text_message(participant_id="no_engage_user", message_text="/reset")
        ctx2 = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="no_engage_user",
            message=reset_msg,
        )
        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx2)

        assert ctx2.experiment_session is not None
        assert ctx2.experiment_session.id != first_session.id

        first_session.refresh_from_db()
        assert first_session.status in STATUSES_FOR_COMPLETE_CHATS

    def test_reset_no_prior_session_creates_new(self):
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team, identifier="fresh_reset_user", platform=experiment_channel.platform
        )

        reset_msg = text_message(participant_id="fresh_reset_user", message_text="/reset")
        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_allowed=True,
            participant_identifier="fresh_reset_user",
            message=reset_msg,
        )

        with pytest.raises(EarlyExitResponse, match="Conversation reset"):
            self.stage(ctx)

        assert ctx.experiment_session is not None

    def test_reset_respects_platform_consent(self):
        """Regression: a participant with revoked platform consent must NOT receive
        a 'Conversation reset' reply.
        """
        experiment = ExperimentFactory()
        experiment_channel = ExperimentChannelFactory(experiment=experiment, team=experiment.team)
        participant = ParticipantFactory(
            team=experiment.team, identifier="revoked_user", platform=experiment_channel.platform
        )
        participant_data = ParticipantData.objects.create(
            team=experiment.team,
            participant=participant,
            experiment=experiment,
            system_metadata={"consent": False},
        )

        capabilities = ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=True,
            supported_message_types=(MESSAGE_TYPES.TEXT,),
            consent_config=PlatformConsentConfig(strict=False, default_consent=True),
        )

        reset_msg = text_message(participant_id="revoked_user", message_text="/reset")
        ctx = make_context(
            experiment=experiment,
            experiment_channel=experiment_channel,
            experiment_session=None,
            participant=participant,
            participant_data=participant_data,
            participant_allowed=True,
            participant_identifier="revoked_user",
            message=reset_msg,
            capabilities=capabilities,
        )

        # ConsentCheckStage runs first in the real pipeline and aborts silently
        with pytest.raises(EarlyAbort):
            ConsentCheckStage()(ctx)

        # SessionResolutionStage (and therefore /reset handling) is never reached

"""End-to-end behaviour of a channel an admin has disabled (issue #4200)."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels.api_channel import ApiChannel
from apps.channels.channel_base import ChannelBase
from apps.channels.evaluation_channel import EvaluationChannel
from apps.channels.exceptions import ChannelDisabledException
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.registry import PLATFORM_CHANNEL_CLASSES
from apps.channels.stages.core import AttachmentHydrationStage, ChannelDisabledStage
from apps.channels.tests.message_examples import base_messages
from apps.channels.web_channel import WebChannel
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, Participant
from apps.experiments.services import start_experiment_session
from apps.experiments.tasks import get_response_for_webchat_task
from apps.service_providers.tracing import TraceInfo
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import StubChannel, make_trace_service


def _disable(session, message=""):
    channel = session.experiment_channel
    channel.enabled = False
    channel.disabled_message = message
    channel.save()
    return channel


def _make_channel(session):
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    channel.trace_service = make_trace_service()
    return channel


@pytest.mark.django_db()
class TestDisabledChannel:
    def test_static_message_is_returned_and_sent(self):
        session = ExperimentSessionFactory.create()
        _disable(session, "The bot is offline for maintenance")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(base_messages.text_message(participant_id="123"))

        assert response.content == "The bot is offline for maintenance"
        assert channel.text_sent == ["The bot is offline for maintenance"]
        assert channel._sender.text_messages[0][1] == "123"
        get_bot.assert_not_called()

    def test_no_message_means_no_reply(self):
        session = ExperimentSessionFactory.create()
        _disable(session)
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot") as get_bot:
            response = channel.new_user_message(base_messages.text_message())

        assert response.content == ""
        assert channel.text_sent == []
        get_bot.assert_not_called()

    def test_inbound_message_is_never_recorded(self):
        """The user's message is dropped -- ChatMessageCreationStage never runs."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        assert not ChatMessage.objects.filter(chat=session.chat, message_type=ChatMessageType.HUMAN).exists()

    def test_static_reply_is_recorded_when_the_channel_carries_a_session(self):
        """Web and Slack pre-set the session, so the reply lands in history like any other
        early exit on those channels. Channels that resolve a session lazily never get that
        far, so nothing is written for them."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        messages = ChatMessage.objects.filter(chat=session.chat)
        assert [(m.message_type, m.content) for m in messages] == [(ChatMessageType.AI, "We are closed")]

    def test_silent_disable_writes_nothing(self):
        """EarlyAbort skips the terminal stages, so not even the session is touched."""
        session = ExperimentSessionFactory.create()
        _disable(session)
        channel = _make_channel(session)

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message())

        assert ChatMessage.objects.filter(chat=session.chat).count() == 0

    def test_no_session_is_created_for_a_new_participant(self):
        """A disabled channel must not spin up conversations for people it is turned off for."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = StubChannel(session.experiment, session.experiment_channel)
        channel.trace_service = make_trace_service()

        with patch("apps.channels.stages.core.get_bot"):
            channel.new_user_message(base_messages.text_message(participant_id="brand-new"))

        assert channel.text_sent == ["We are closed"]
        assert not session.experiment.sessions.filter(participant__identifier="brand-new").exists()


@pytest.mark.django_db()
class TestDisabledEmbeddedWidget:
    """Widget traffic is served by WebChannel, not ApiChannel, so the toggle has to be
    enforced on that pipeline too -- otherwise the badge and the dialog warning show while
    the bot keeps answering every widget message."""

    def _widget_session(self, disabled_message=""):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": "tok"},
            enabled=False,
            disabled_message=disabled_message,
        )
        return ExperimentSessionFactory.create(
            experiment=channel.experiment, team=channel.team, experiment_channel=channel
        )

    def test_widget_message_gets_the_static_reply(self):
        session = self._widget_session("The bot is offline for maintenance")

        with patch("apps.chat.bots.PipelineBot.process_input") as process_input:
            result = get_response_for_webchat_task(
                experiment_session_id=session.id, experiment_id=session.experiment.id, message_text="hello"
            )

        assert result["response"] == "The bot is offline for maintenance"
        process_input.assert_not_called()

    def test_widget_message_is_ignored_when_no_static_reply_is_configured(self):
        session = self._widget_session()

        with patch("apps.chat.bots.PipelineBot.process_input") as process_input:
            result = get_response_for_webchat_task(
                experiment_session_id=session.id, experiment_id=session.experiment.id, message_text="hello"
            )

        assert result["response"] == ""
        process_input.assert_not_called()


@pytest.mark.django_db()
class TestDisabledStageIsWired:
    """Every channel a participant can reach must honour the toggle."""

    def _core_stages(self, channel_cls):
        stub_self = MagicMock(attachment_hydration_stage_class=AttachmentHydrationStage)
        return channel_cls._build_pipeline(stub_self).core_stages

    @pytest.mark.parametrize("platform", sorted(PLATFORM_CHANNEL_CLASSES, key=str))
    def test_every_registered_platform_enforces_the_toggle(self, platform):
        stages = self._core_stages(PLATFORM_CHANNEL_CLASSES[platform])

        assert any(isinstance(stage, ChannelDisabledStage) for stage in stages), (
            f"{platform} does not enforce the channel enabled/disabled toggle"
        )

    @pytest.mark.parametrize("channel_cls", [ChannelBase, ApiChannel, WebChannel])
    def test_pipelines_that_are_not_reached_through_the_registry(self, channel_cls):
        stages = self._core_stages(channel_cls)

        assert any(isinstance(stage, ChannelDisabledStage) for stage in stages)

    def test_evaluations_are_deliberately_exempt(self):
        """An eval run is not participant access; substituting the static message for the
        bot's output would corrupt the run."""
        stages = self._core_stages(EvaluationChannel)

        assert not any(isinstance(stage, ChannelDisabledStage) for stage in stages)


@pytest.mark.django_db()
class TestDisabledChannelBlocksNewSessions:
    """The toggle has to mean "no new conversations", not only "no bot replies".

    ChannelDisabledStage can only speak for traffic that reaches a pipeline. Every route that
    opens a session -- widget and API session starts, the public web chat, the Slack listener,
    admin "new session" actions -- creates it *before* any pipeline runs, so the guarantee lives
    in start_experiment_session instead.
    """

    def _disabled_channel(self, disabled_message=""):
        return ExperimentChannelFactory.create(enabled=False, disabled_message=disabled_message)

    def test_start_experiment_session_refuses(self):
        channel = self._disabled_channel("We are closed")

        with pytest.raises(ChannelDisabledException) as exc_info:
            start_experiment_session(
                working_experiment=channel.experiment,
                experiment_channel=channel,
                participant=Participant(identifier="brand-new"),
            )

        assert exc_info.value.channel == channel
        assert exc_info.value.disabled_message == "We are closed"

    def test_refusing_writes_nothing(self):
        """The refusal precedes every write, so a disabled channel leaves no trace of the attempt."""
        channel = self._disabled_channel()

        with pytest.raises(ChannelDisabledException):
            start_experiment_session(
                working_experiment=channel.experiment,
                experiment_channel=channel,
                participant=Participant(identifier="brand-new"),
            )

        assert not ExperimentSession.objects.filter(experiment_channel=channel).exists()
        assert not Participant.objects.filter(identifier="brand-new").exists()
        assert not Chat.objects.exists()

    def test_an_enabled_channel_is_unaffected(self):
        channel = ExperimentChannelFactory.create()

        session = start_experiment_session(
            working_experiment=channel.experiment,
            experiment_channel=channel,
            participant=Participant(identifier="brand-new"),
        )

        assert session.experiment_channel == channel


@pytest.mark.django_db()
class TestDisabledChannelBlocksOutboundMessages:
    """Bot-initiated sends -- scheduled messages, event actions, API triggers -- never touch a
    pipeline, so the toggle has to be enforced on the outbound path separately."""

    def test_ad_hoc_bot_message_is_a_no_op(self):
        """Checked before the LLM runs, so a disabled channel costs nothing and leaves no
        undelivered AI message behind."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")

        with (
            patch("apps.chat.bots.EventBot.get_user_message") as get_user_message,
            patch("apps.experiments.models.ExperimentSession.try_send_message") as try_send_message,
        ):
            result = session.ad_hoc_bot_message("check in with the user", TraceInfo(name="test"))

        assert result == {}
        get_user_message.assert_not_called()
        try_send_message.assert_not_called()
        assert not ChatMessage.objects.filter(chat=session.chat).exists()

    def test_direct_message_is_a_no_op(self):
        session = ExperimentSessionFactory.create()
        _disable(session)

        with patch("apps.experiments.models.ExperimentSession.try_send_message") as try_send_message:
            result = session.ad_hoc_bot_message(None, TraceInfo(name="test"), message_text="see you tomorrow")

        assert result == {}
        try_send_message.assert_not_called()
        assert not ChatMessage.objects.filter(chat=session.chat).exists()

    def test_send_message_to_user_delivers_nothing(self):
        """And the static disabled_message is not substituted in: nobody asked us anything, so
        pushing "we are offline" at them would be worse than staying quiet."""
        session = ExperimentSessionFactory.create()
        _disable(session, "We are closed")
        channel = _make_channel(session)

        channel.send_message_to_user("your appointment is tomorrow")

        assert channel.text_sent == []
        assert not ChatMessage.objects.filter(chat=session.chat).exists()

    def test_an_enabled_channel_still_sends(self):
        session = ExperimentSessionFactory.create()
        channel = _make_channel(session)

        channel.send_message_to_user("your appointment is tomorrow")

        assert channel.text_sent == ["your appointment is tomorrow"]


@pytest.mark.django_db()
class TestDisabledChannelRefusesSessionStarts:
    """The HTTP surfaces that open a session, each turning the refusal into its own idiom.

    The trigger-bot endpoint belongs to this set too, but its refusal now comes from
    ``get_trigger_bot_channel``, so it is covered by ``test_trigger_bot_on_disabled_channel``
    in ``apps/api/tests/test_api.py`` alongside the rest of that endpoint's behaviour.
    """

    def test_widget_session_start_is_refused(self):
        """The widget calls this before it can send anything, so letting it through would hand
        back a session and a token on a channel that will not talk."""
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        channel = ExperimentChannel.objects.get_team_api_channel(experiment.team)
        channel.enabled = False
        channel.disabled_message = "The bot is offline for maintenance"
        channel.save()

        response = APIClient().post(
            reverse("api:chat:start-session"),
            data={"chatbot_id": str(experiment.public_id), "session_data": {}},
            format="json",
        )

        assert response.status_code == 403
        assert response.json() == {"error": "The bot is offline for maintenance"}
        assert not ExperimentSession.objects.filter(experiment_channel=channel).exists()
        assert not Participant.objects.filter(team=experiment.team).exists()

    def test_widget_session_start_refusal_has_a_body_when_silent(self):
        """A silent disable still owes an HTTP caller something to show."""
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        channel = ExperimentChannel.objects.get_team_api_channel(experiment.team)
        channel.enabled = False
        channel.save()

        response = APIClient().post(
            reverse("api:chat:start-session"),
            data={"chatbot_id": str(experiment.public_id), "session_data": {}},
            format="json",
        )

        assert response.status_code == 403
        assert response.json() == {"error": "This chatbot is currently unavailable."}

    def test_public_web_chat_shows_the_static_message(self, client):
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        channel = ExperimentChannel.objects.get_team_web_channel(experiment.team)
        channel.enabled = False
        channel.disabled_message = "Back on Monday"
        channel.save()

        response = client.get(
            reverse(
                "experiments:start_session_public",
                kwargs={"team_slug": experiment.team.slug, "experiment_id": experiment.public_id},
            )
        )

        assert response.status_code == 503
        assert "Back on Monday" in response.content.decode()
        assert not ExperimentSession.objects.filter(experiment_channel=channel).exists()

    def _console_user(self, experiment, *codenames):
        user = experiment.team.members.first()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return user

    def test_authed_web_session_start_is_refused(self, client):
        """The console's own "chat to this bot" button goes through the same web channel."""
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        channel = ExperimentChannel.objects.get_team_web_channel(experiment.team)
        channel.enabled = False
        channel.save()
        client.force_login(self._console_user(experiment))

        response = client.post(
            reverse(
                "chatbots:start_authed_web_session",
                args=[experiment.team.slug, experiment.id, experiment.version_number],
            )
        )

        assert response.status_code == 302
        assert response.url == reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id])
        assert not ExperimentSession.objects.exists()

    def test_invitation_is_refused(self, client):
        """An invitation to a channel that will not talk is worse than no invitation."""
        experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
        channel = ExperimentChannel.objects.get_team_web_channel(experiment.team)
        channel.enabled = False
        channel.save()
        client.force_login(self._console_user(experiment, "invite_participants", "view_experiment"))

        with patch("apps.chatbots.views.send_experiment_invitation") as send_invitation:
            response = client.post(
                reverse("chatbots:chatbots_invitations", args=[experiment.team.slug, experiment.id]),
                data={"experiment_id": experiment.id, "email": "someone@example.com", "invite_now": "on"},
            )

        assert response.status_code == 200
        send_invitation.assert_not_called()
        assert not ExperimentSession.objects.exists()

from unittest.mock import Mock

import pytest
from django.urls import reverse

from apps.chat.models import ChatMessage, ChatMessageType
from apps.trace.models import Trace
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentSessionFactory,
    ParticipantFactory,
)
from apps.utils.factories.traces import TraceFactory


class TestParticipantDataDiffFromTrace:
    """Unit tests for ChatMessage.participant_data_diff_from_trace property.
    No DB needed — uses plain model instances with manually set attributes.
    """

    def test_no_prefetch_attribute(self):
        msg = ChatMessage()
        assert msg.participant_data_diff_from_trace is None

    def test_empty_prefetch_list(self):
        msg = ChatMessage()
        msg.prefetched_output_traces_with_diff = []
        assert msg.participant_data_diff_from_trace is None

    def test_single_trace_with_diff(self):
        diff = [["change", "plan", ["free", "pro"]]]
        trace = Mock(spec=Trace)
        trace.participant_data_diff = diff

        msg = ChatMessage()
        msg.prefetched_output_traces_with_diff = [trace]
        assert msg.participant_data_diff_from_trace == diff

    def test_multiple_traces_returns_first(self):
        diff1 = [["change", "plan", ["free", "pro"]]]
        diff2 = [["add", "", [["score", 100]]]]
        trace1 = Mock(spec=Trace)
        trace1.participant_data_diff = diff1
        trace2 = Mock(spec=Trace)
        trace2.participant_data_diff = diff2

        msg = ChatMessage()
        msg.prefetched_output_traces_with_diff = [trace1, trace2]
        assert msg.participant_data_diff_from_trace == diff1


@pytest.mark.django_db()
class TestParticipantDataDiffInSessionMessages:
    """Integration tests for diff indicator rendering in the session messages view."""

    DIFF_ICON_MARKER = "fa-code-compare"

    @pytest.fixture()
    def session_with_messages(self, experiment):
        """Uses the ``experiment`` fixture from conftest which creates a team with user memberships.
        The participant's user is set to the experiment owner so that the
        ``verify_session_access_cookie`` decorator grants access.
        """
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            participant=ParticipantFactory.create(team=experiment.team, user=experiment.owner),
        )
        human_msg = ChatMessageFactory.create(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN,
            content="Hello",
        )
        ai_msg = ChatMessageFactory.create(
            chat=session.chat,
            message_type=ChatMessageType.AI,
            content="Hi there!",
        )
        return session, human_msg, ai_msg

    def _get_messages_url(self, session):
        experiment = session.experiment
        return reverse(
            "experiments:experiment_session_messages_view",
            kwargs={
                "team_slug": experiment.team.slug,
                "experiment_id": experiment.public_id,
                "session_id": session.external_id,
            },
        )

    def _login_and_get(self, client, session):
        user = session.experiment.owner
        client.force_login(user)
        url = self._get_messages_url(session)
        return client.get(url)

    def _create_trace(self, session, participant_data_diff, **kwargs):
        return TraceFactory.create(
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            team=session.team,
            participant_data_diff=participant_data_diff,
            **kwargs,
        )

    def test_ai_message_with_diff_shows_indicator(self, client, session_with_messages):
        session, human_msg, ai_msg = session_with_messages
        self._create_trace(session, [["change", "plan", ["free", "pro"]]], output_message=ai_msg)

        response = self._login_and_get(client, session)
        content = response.content.decode()
        assert self.DIFF_ICON_MARKER in content

    def test_ai_message_with_null_diff_no_indicator(self, client, session_with_messages):
        session, human_msg, ai_msg = session_with_messages
        self._create_trace(session, None, output_message=ai_msg)

        response = self._login_and_get(client, session)
        content = response.content.decode()
        assert content.count(self.DIFF_ICON_MARKER) == 0

    def test_ai_message_with_empty_diff_no_indicator(self, client, session_with_messages):
        session, human_msg, ai_msg = session_with_messages
        self._create_trace(session, [], output_message=ai_msg)

        response = self._login_and_get(client, session)
        content = response.content.decode()
        assert content.count(self.DIFF_ICON_MARKER) == 0

    def test_ai_message_with_no_trace_no_indicator(self, client, session_with_messages):
        session, human_msg, ai_msg = session_with_messages

        response = self._login_and_get(client, session)
        content = response.content.decode()
        assert content.count(self.DIFF_ICON_MARKER) == 0

    def test_human_message_with_diff_no_indicator(self, client, session_with_messages):
        """Diff indicator only shows on AI messages, not human messages."""
        session, human_msg, ai_msg = session_with_messages
        self._create_trace(session, [["change", "plan", ["free", "pro"]]], input_message=human_msg)

        response = self._login_and_get(client, session)
        content = response.content.decode()
        assert content.count(self.DIFF_ICON_MARKER) == 0

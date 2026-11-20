from unittest.mock import MagicMock, patch

import pytest

from apps.chat.models import ChatMessageType
from apps.service_providers.tracing import TraceInfo
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory


@pytest.mark.django_db()
class TestAdHocBotMessage:
    """Scheduled messages, reminders and timeout events all send through `ad_hoc_bot_message`."""

    def _blocked_session(self):
        session = ExperimentSessionFactory.create()
        DeniedParticipantFactory.create(team=session.team, participant=session.participant)
        return session

    @pytest.mark.parametrize(
        ("instruction_prompt", "message_text"),
        [pytest.param("check in with the user", None, id="prompt"), pytest.param(None, "see you", id="direct")],
    )
    def test_skipped_for_blocked_participant(self, instruction_prompt, message_text):
        session = self._blocked_session()

        with (
            patch("apps.chat.bots.EventBot.get_user_message") as get_user_message,
            patch("apps.experiments.models.ExperimentSession.try_send_message") as try_send_message,
        ):
            result = session.ad_hoc_bot_message(instruction_prompt, TraceInfo(name="test"), message_text=message_text)

        assert result == {}
        get_user_message.assert_not_called()
        try_send_message.assert_not_called()
        session.refresh_from_db()
        assert session.ended_at is None

    @pytest.mark.parametrize(
        ("publish", "expected_tag"),
        [pytest.param(True, "v1", id="published"), pytest.param(False, "v1-unreleased", id="unpublished")],
    )
    def test_defaults_to_published_version(self, publish, expected_tag):
        session = ExperimentSessionFactory.create()
        if publish:
            session.experiment.create_new_version(make_default=True)
        provider = MagicMock()
        provider.get_llm_service.return_value.get_chat_model.return_value.invoke.return_value.text = "Are you there?"

        with (
            patch("apps.chat.bots.EventBot.get_provider_and_model", return_value=(provider, MagicMock())),
            patch("apps.chat.bots.get_model_parameters", return_value={}),
            patch("apps.experiments.models.ExperimentSession.try_send_message"),
        ):
            session.ad_hoc_bot_message("check in with the user", TraceInfo(name="test"))

        message = session.chat.messages.get(message_type=ChatMessageType.AI)
        assert [tag.name for tag in message.tags.all()] == [expected_tag]

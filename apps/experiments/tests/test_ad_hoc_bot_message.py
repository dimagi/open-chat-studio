import logging
from unittest.mock import MagicMock, patch

import anthropic
import openai
import pytest

from apps.chat.exceptions import ModelRefusedTurnError, ProviderConfigurationError
from apps.chat.models import ChatMessageType
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.tests.provider_errors import status_error

USAGE_LIMIT_ERROR = status_error(
    anthropic.BadRequestError,
    400,
    "You have reached your specified API usage limits. You will regain access on 2026-12-01 at 00:00 UTC.",
)
CONTENT_FILTER_ERROR = status_error(openai.BadRequestError, 400, "content filter", body={"code": "content_filter"})


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
        ("scenario", "expected_tag", "expected_version_number"),
        [
            pytest.param("published", "v1", 1, id="published"),
            pytest.param("unpublished", "v1-unreleased", 1, id="unpublished"),
            pytest.param("snapshot_session", "v1", 1, id="session-bound-to-snapshot"),
            pytest.param("explicit_working", "v2-unreleased", 2, id="explicit-use-experiment-wins"),
        ],
    )
    def test_version_used_for_message(self, scenario, expected_tag, expected_version_number):
        session = ExperimentSessionFactory.create()
        working = session.experiment
        use_experiment = None
        if scenario in ("published", "explicit_working"):
            working.create_new_version(make_default=True)
        if scenario == "explicit_working":
            use_experiment = working
        if scenario == "snapshot_session":
            snapshot = working.create_new_version(make_default=True)
            working.create_new_version(make_default=True)
            session.experiment = snapshot
            session.save()
        provider = MagicMock()
        provider.get_llm_service.return_value.get_chat_model.return_value.invoke.return_value.text = "Are you there?"

        with (
            patch("apps.chat.bots.EventBot.get_provider_and_model", return_value=(provider, MagicMock())),
            patch("apps.chat.bots.get_model_parameters", return_value={}),
            patch("apps.experiments.models.ExperimentSession.try_send_message"),
            patch(
                "apps.experiments.models.TracingService.create_for_experiment",
                wraps=TracingService.create_for_experiment,
            ) as create_for_experiment,
        ):
            session.ad_hoc_bot_message("check in with the user", TraceInfo(name="test"), use_experiment=use_experiment)

        message = session.chat.messages.get(message_type=ChatMessageType.AI)
        assert [tag.name for tag in message.tags.all()] == [expected_tag]
        assert create_for_experiment.call_args.args[0].version_number == expected_version_number

    def _session_with_failing_llm(self, error):
        session = ExperimentSessionFactory.create()
        provider = MagicMock()
        provider.get_llm_service.return_value.get_chat_model.return_value.invoke.side_effect = error
        return session, provider

    def test_provider_configuration_error_is_logged_as_warning(self, caplog):
        """An exhausted provider quota is the team's to fix, so it must not be logged at error level (Sentry)."""
        session, provider = self._session_with_failing_llm(USAGE_LIMIT_ERROR)

        with (
            patch("apps.chat.bots.EventBot.get_provider_and_model", return_value=(provider, MagicMock())),
            patch("apps.chat.bots.get_model_parameters", return_value={}),
            patch("apps.experiments.models.ExperimentSession.try_send_message") as try_send_message,
            caplog.at_level(logging.WARNING, logger="ocs.experiments"),
        ):
            assert session.ad_hoc_bot_message("check in with the user", TraceInfo(name="test")) is None

        try_send_message.assert_not_called()
        assert [r.levelno for r in caplog.records if r.name == "ocs.experiments"] == [logging.WARNING]

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            pytest.param(USAGE_LIMIT_ERROR, ProviderConfigurationError, id="quota-exhausted"),
            pytest.param(CONTENT_FILTER_ERROR, ModelRefusedTurnError, id="content-filter"),
        ],
    )
    def test_translated_provider_error_is_raised_when_not_failing_silently(self, error, expected):
        session, provider = self._session_with_failing_llm(error)

        with (
            patch("apps.chat.bots.EventBot.get_provider_and_model", return_value=(provider, MagicMock())),
            patch("apps.chat.bots.get_model_parameters", return_value={}),
            pytest.raises(expected),
        ):
            session.ad_hoc_bot_message("check in with the user", TraceInfo(name="test"), fail_silently=False)

from unittest import mock

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError

from apps.chat.models import ChatMessage, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationDetail
from apps.experiments.models import Participant
from apps.moderation.const import MODERATION_WAIT_TIMEOUT, STRIKE_LIMIT
from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.moderation.services import (
    add_strike,
    get_moderation_result,
    record_moderation_result,
    record_rule_hits,
    remove_turn_from_pipeline_histories,
    start_moderation,
    strike_categories,
)
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatHistoryTypes
from apps.pipelines.repository import ORMRepository
from apps.service_providers.moderation_service.schemas import ModerationCategory, ModerationOutput
from apps.utils.factories.experiment import (
    ChatFactory,
    ChatMessageFactory,
    ExperimentSessionFactory,
)
from apps.utils.factories.moderation import DeniedParticipantFactory
from apps.utils.factories.pipelines import PipelineChatHistoryFactory, PipelineChatMessagesFactory
from apps.utils.factories.service_provider_factories import ModerationProviderFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.llm_messages import EMPTY_MESSAGE_PLACEHOLDER

OUTPUT = ModerationOutput({"harassment": ModerationCategory(flagged=False, score=0.1)})


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("text", "initial", "expected"),
    [
        pytest.param("hello", None, None, id="no_hit"),
        pytest.param("<script>", None, ModerationDetail(rules=["markup"]), id="hit"),
        pytest.param(
            "<script>",
            ModerationDetail(output=OUTPUT),
            ModerationDetail(output=OUTPUT, rules=["markup"]),
            id="hit_keeps_existing_detail",
        ),
    ],
)
def test_record_rule_hits(text, initial, expected):
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content=text, moderation_detail=initial
    )

    record_rule_hits(message=message, text=text, attachments=[], participant=None)

    message.refresh_from_db()
    assert message.moderation_detail == expected


@pytest.mark.django_db()
def test_record_rule_hits_updates_updated_at():
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="<script>"
    )
    before = message.updated_at

    record_rule_hits(message=message, text="<script>", attachments=[], participant=None)

    message.refresh_from_db()
    assert message.updated_at > before


def _output(flags: dict[str, bool]) -> ModerationOutput:
    """An output that flags the categories set to True."""
    return ModerationOutput(
        {name: ModerationCategory(flagged=flagged, score=0.9 if flagged else 0.1) for name, flagged in flags.items()}
    )


def _human_message(detail: ModerationDetail | None = None):
    return ChatMessageFactory.create(
        chat=ChatFactory.create(), message_type=ChatMessageType.HUMAN, content="text", moderation_detail=detail
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("output", "skip_reason", "expected_status"),
    [
        pytest.param(_output({"harassment": False, "illicit": False}), None, ModerationStatus.PASSED, id="passed"),
        pytest.param(
            _output({"harassment": True, "illicit": False}), None, ModerationStatus.PASSED, id="non_strike_flagged"
        ),
        pytest.param(
            _output({"harassment": True, "illicit": True}), None, ModerationStatus.FLAGGED, id="strike_flagged"
        ),
        pytest.param(None, "timeout", ModerationStatus.SKIPPED, id="skipped"),
    ],
)
def test_record_moderation_result(output, skip_reason, expected_status):
    message = _human_message()

    status = record_moderation_result(message=message, output=output, skip_reason=skip_reason)

    message.refresh_from_db()
    assert status == expected_status
    assert message.moderation_status == expected_status
    assert message.moderation_detail == ModerationDetail(output=output, skip_reason=skip_reason)


@pytest.mark.django_db()
def test_record_moderation_result_keeps_rules_and_updates_updated_at():
    message = _human_message(ModerationDetail(rules=["markup"]))
    before = message.updated_at

    record_moderation_result(message=message, output=OUTPUT)

    message.refresh_from_db()
    assert message.moderation_detail == ModerationDetail(output=OUTPUT, rules=["markup"])
    assert message.updated_at > before


@pytest.mark.parametrize(
    ("output", "skip_reason"),
    [
        pytest.param(None, None, id="neither"),
        pytest.param(OUTPUT, "error", id="both"),
    ],
)
def test_record_moderation_result_needs_exactly_one_outcome(output, skip_reason):
    with pytest.raises(ValueError, match="exactly one"):
        record_moderation_result(message=ChatMessage(), output=output, skip_reason=skip_reason)


def test_strike_categories():
    assert strike_categories(_output({"harassment": True, "illicit": True, "sexual/minors": False})) == {"illicit"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("moderation_enabled", "has_provider", "text", "started"),
    [
        pytest.param(True, True, "hello", True, id="started"),
        pytest.param(False, True, "hello", False, id="moderation_off"),
        pytest.param(True, False, "hello", False, id="no_provider"),
        pytest.param(True, True, "", False, id="empty_text"),
        pytest.param(True, True, "  ", False, id="whitespace"),
        pytest.param(True, True, EMPTY_MESSAGE_PLACEHOLDER, False, id="empty_message_placeholder"),
    ],
)
def test_start_moderation(moderation_enabled, has_provider, text, started):
    team = TeamFactory.create(moderation_enabled=moderation_enabled)
    if has_provider:
        team.moderation_provider = ModerationProviderFactory.create(team=team)
        team.save()
    message = ChatMessageFactory.create(
        chat=ChatFactory.create(team=team), message_type=ChatMessageType.HUMAN, content=text or ""
    )

    with mock.patch("apps.moderation.services.moderate_message") as task:
        task.delay.return_value.id = "task-1"
        task_id = start_moderation(team=team, message=message)

    assert task_id == ("task-1" if started else None)
    if started:
        task.delay.assert_called_once_with(
            chat_message_id=message.id, moderation_provider_id=team.moderation_provider_id
        )
    else:
        task.delay.assert_not_called()


@pytest.mark.parametrize(
    ("wait", "expected"),
    [
        pytest.param(
            {"return_value": {"status": "ok", "output": {"hate": {"flagged": False, "score": 0.1}}}},
            (ModerationOutput({"hate": ModerationCategory(flagged=False, score=0.1)}), None),
            id="ok",
        ),
        pytest.param(
            {"return_value": {"status": "skipped", "reason": "rate_limited"}}, (None, "rate_limited"), id="skipped"
        ),
        pytest.param({"side_effect": CeleryTimeoutError()}, (None, "timeout"), id="timeout"),
        pytest.param({"side_effect": RuntimeError("boom")}, (None, "error"), id="task_raised"),
        pytest.param(
            {"return_value": {"status": "ok", "output": {"hate": {"flagged": True, "score": 2}}}},
            (None, "error"),
            id="invalid_output",
        ),
    ],
)
def test_get_moderation_result(wait, expected):
    with mock.patch("apps.moderation.services.AsyncResult") as async_result:
        async_result.return_value.get.configure_mock(**wait)
        result = get_moderation_result("task-1")

    assert result == expected
    async_result.assert_called_once_with("task-1")
    async_result.return_value.get.assert_called_once_with(
        timeout=MODERATION_WAIT_TIMEOUT, disable_sync_subtasks=False, interval=0.1
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "history_type",
    [
        pytest.param(PipelineChatHistoryTypes.NODE, id="node_history"),
        pytest.param(PipelineChatHistoryTypes.NAMED, id="named_history"),
    ],
)
def test_remove_turn_from_pipeline_histories(history_type):
    session = ExperimentSessionFactory.create()
    other_session = ExperimentSessionFactory.create()
    history = PipelineChatHistoryFactory.create(session=session, type=history_type, name="history")
    other_history = PipelineChatHistoryFactory.create(session=other_session, type=history_type, name="history")
    PipelineChatMessagesFactory.create(chat_history=history, human_message="Earlier", ai_message="Earlier answer")
    human_message = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Bad")
    PipelineChatMessagesFactory.create(chat_history=history, human_message="Bad", ai_message="Original answer")
    PipelineChatMessagesFactory.create(chat_history=other_history, human_message="Other", ai_message="Other answer")
    bot_response = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.AI, content="Answer")

    remove_turn_from_pipeline_histories(human_message=human_message, bot_response=bot_response)

    node_history = ORMRepository(session=session).get_pipeline_chat_history(history_type, "history")
    messages = node_history.get_langchain_messages_until_marker(PipelineChatHistoryModes.TRUNCATE_TOKENS)
    assert [message.content for message in messages] == ["Earlier", "Earlier answer"]
    assert other_history.messages.count() == 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(CeleryTimeoutError(), id="timeout"),
        pytest.param(RuntimeError("boom"), id="task_raised"),
    ],
)
def test_get_moderation_result_logs_a_failed_task_as_a_warning(error, caplog):
    with mock.patch("apps.moderation.services.AsyncResult") as async_result:
        async_result.return_value.get.side_effect = error
        get_moderation_result("task-1")

    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert caplog.records[0].exc_info is None


BLOCKED_NOTIFICATION = "apps.moderation.services.participant_auto_blocked_notification"


@pytest.fixture()
def auto_block_session():
    """A session whose team has auto-block on, with a participant who has no linked user."""
    session = ExperimentSessionFactory.create()
    session.experiment.team.auto_block_enabled = True
    session.experiment.team.save()
    return session


def _add_strike(session, categories=("illicit",)):
    message = ChatMessageFactory.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="text")
    add_strike(
        participant=session.participant, experiment=session.experiment, chat_message=message, categories=categories
    )
    return message


@pytest.mark.django_db()
class TestAddStrike:
    def test_increments_from_the_database_value(self, auto_block_session):
        """Two increments made through stale copies of the participant both count."""
        first = Participant.objects.get(id=auto_block_session.participant_id)
        second = Participant.objects.get(id=auto_block_session.participant_id)
        message = _add_strike(auto_block_session)

        add_strike(participant=first, experiment=auto_block_session.experiment, chat_message=message, categories=[])
        add_strike(participant=second, experiment=auto_block_session.experiment, chat_message=message, categories=[])

        assert second.strikes == 3
        assert Participant.objects.get(id=auto_block_session.participant_id).strikes == 3

    def test_updates_updated_at(self, auto_block_session):
        before = auto_block_session.participant.updated_at

        _add_strike(auto_block_session)

        assert Participant.objects.get(id=auto_block_session.participant_id).updated_at > before

    def test_auto_block_off_gives_no_strike(self, auto_block_session):
        auto_block_session.experiment.team.auto_block_enabled = False
        auto_block_session.experiment.team.save()

        with mock.patch(BLOCKED_NOTIFICATION) as notification:
            for _ in range(STRIKE_LIMIT):
                _add_strike(auto_block_session)

        assert Participant.objects.get(id=auto_block_session.participant_id).strikes == 0
        assert not DeniedParticipant.objects.exists()
        notification.assert_not_called()

    @pytest.mark.parametrize(
        "is_member",
        [
            pytest.param(True, id="team_member_is_exempt"),
            pytest.param(False, id="user_from_another_team_is_not_exempt"),
        ],
    )
    def test_exempt_participant_gets_no_strike(self, auto_block_session, is_member):
        team = auto_block_session.experiment.team
        membership = MembershipFactory.create(team=team if is_member else TeamFactory.create())
        participant = auto_block_session.participant
        participant.user = membership.user
        participant.save()

        _add_strike(auto_block_session)

        assert Participant.objects.get(id=participant.id).strikes == (0 if is_member else 1)

    def test_blocks_at_the_strike_limit_only(self, auto_block_session):
        with mock.patch(BLOCKED_NOTIFICATION) as notification:
            for _ in range(STRIKE_LIMIT - 1):
                _add_strike(auto_block_session)
            assert not DeniedParticipant.objects.exists()
            notification.assert_not_called()

            message = _add_strike(auto_block_session, categories={"illicit/violent", "hate/threatening"})

        denial = DeniedParticipant.objects.get(participant=auto_block_session.participant)
        assert denial.team == auto_block_session.experiment.team
        assert denial.source == DeniedParticipantSource.AUTO
        assert denial.experiment == auto_block_session.experiment
        assert denial.chat_message == message
        assert denial.reason == "hate/threatening, illicit/violent"
        notification.assert_called_once_with(
            experiment=auto_block_session.experiment,
            participant=auto_block_session.participant,
            reason="hate/threatening, illicit/violent",
        )

    def test_notifies_once(self, auto_block_session):
        with mock.patch(BLOCKED_NOTIFICATION) as notification:
            for _ in range(STRIKE_LIMIT + 2):
                _add_strike(auto_block_session)

        notification.assert_called_once()
        assert DeniedParticipant.objects.filter(participant=auto_block_session.participant).count() == 1
        assert Participant.objects.get(id=auto_block_session.participant_id).strikes == STRIKE_LIMIT + 2

    def test_keeps_an_existing_block(self, auto_block_session):
        existing = DeniedParticipantFactory.create(
            team=auto_block_session.experiment.team, participant=auto_block_session.participant, reason="Manual"
        )

        with mock.patch(BLOCKED_NOTIFICATION) as notification:
            for _ in range(STRIKE_LIMIT):
                _add_strike(auto_block_session)

        assert list(DeniedParticipant.objects.all()) == [existing]
        notification.assert_not_called()

"""End-to-end abuse detection scenarios through the real channel pipelines.

Only the outside world is faked: the OpenAI moderation endpoint (at the HTTP layer, with pytest-httpx), the
bot's LLM, and the Telegram Bot API client. The `moderate_message` task runs eagerly and stores its result in
the Celery result backend, so `get_moderation_result` reads it back by task id as it does in production.
"""

import itertools
from dataclasses import dataclass
from unittest import mock

import httpx
import pytest
from django.test import TestCase, override_settings
from django.urls import reverse
from field_audit import enable_audit
from field_audit.models import AuditEvent
from langchain_core.messages import BaseMessage
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform, WidgetAuthLevel
from apps.channels.tasks import handle_telegram_message
from apps.channels.tests.message_examples import api_messages
from apps.chat.models import ChatMessage, ChatMessageType, ModerationStatus
from apps.chat.schemas import ModerationSkipReason
from apps.experiments.models import Experiment, Participant
from apps.experiments.tasks import get_response_for_webchat_task
from apps.moderation.enforcement import BLOCKED_MESSAGE, block_participant
from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.moderation.tasks import moderate_message
from apps.ocs_notifications.models import NotificationEvent
from apps.pipelines.models import PipelineChatHistoryTypes
from apps.pipelines.tests.utils import (
    code_node,
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.service_providers.models import LlmProvider
from apps.teams.flags import Flags
from apps.teams.models import Team
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    ModerationProviderFactory,
)
from apps.utils.factories.team import MembershipFactory, UserFactory
from apps.utils.tests.clients import ApiTestClient
from apps.utils.tests.langchain import FakeLlm, FakeLlmService
from apps.utils.tests.moderation_helpers import select_moderation_provider

FLAG = Flags.ABUSE_DETECTION.slug
MODERATIONS_URL = "https://api.openai.com/v1/moderations"
TELEGRAM_CHAT_ID = 4242
REPLACEMENT_REPLY = "Sorry, I can't respond to that message."
OFFENSIVE_TEXT = "I am going to hurt you"
WIDGET_TOKEN = "e2e_widget_token_1234567890abcd"
BLOCKED_BODY = {"code": "participant_blocked", "detail": BLOCKED_MESSAGE}

CATEGORIES = (
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/intent",
    "self-harm/instructions",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
)
STRIKE = ("illicit/violent", "harassment/threatening")
"""Flagged categories that give a strike."""

_ids = itertools.count(1000)


def _moderation_json(*flagged: str) -> dict:
    """An OpenAI `/v1/moderations` response body that flags `flagged`."""
    return {
        "id": f"modr-{next(_ids)}",
        "model": "omni-moderation-latest",
        "results": [
            {
                "flagged": bool(flagged),
                "categories": {name: name in flagged for name in CATEGORIES},
                "category_scores": {name: 0.93 if name in flagged else 0.0012 for name in CATEGORIES},
                "category_applied_input_types": {name: ["text"] for name in CATEGORIES},
            }
        ],
    }


def _moderation_returns(httpx_mock, *flagged: str) -> None:
    httpx_mock.add_response(method="POST", url=MODERATIONS_URL, json=_moderation_json(*flagged))


def _is_event_bot_prompt(messages: list[BaseMessage]) -> bool:
    return messages[0].type == "system" and "Your role is to generate messages to send to users" in messages[0].text


class ScriptedLlm(FakeLlm):
    """Numbers the bot's answers ("Bot answer 1", ...) and gives EventBot a fixed replacement reply."""

    responses: list = []

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str:
        self.calls.append(mock.call(messages, *args, **kwargs))
        if _is_event_bot_prompt(messages):
            return REPLACEMENT_REPLY
        return f"Bot answer {len(self.bot_prompts())}"

    def bot_prompts(self) -> list[list[BaseMessage]]:
        """The messages the chatbot's LLM node sent on each turn, excluding EventBot calls."""
        return [call.args[0] for call in self.calls if not _is_event_bot_prompt(call.args[0])]


@dataclass
class Chatbot:
    team: Team
    experiment: Experiment
    telegram_bot: mock.MagicMock
    llm: ScriptedLlm

    def send_telegram(self, text: str) -> list[str]:
        """Deliver a Telegram update to the channel task and return the texts the bot sent back."""
        sent_before = len(self.telegram_texts)
        message_id = next(_ids)
        update = {
            "update_id": message_id,
            "message": {
                "message_id": message_id,
                "from": {"id": TELEGRAM_CHAT_ID, "is_bot": False, "first_name": "Sam", "language_code": "en"},
                "chat": {"id": TELEGRAM_CHAT_ID, "first_name": "Sam", "type": "private"},
                "date": 1760000000,
                "text": text,
            },
        }
        channel = self.experiment.experimentchannel_set.get(platform=ChannelPlatform.TELEGRAM)
        with TestCase.captureOnCommitCallbacks(execute=True):
            handle_telegram_message(message_data=update, channel_external_id=channel.external_id)
        return self.telegram_texts[sent_before:]

    @property
    def telegram_texts(self) -> list[str]:
        return [call.kwargs["text"] for call in self.telegram_bot.send_message.call_args_list]

    def telegram_participant(self) -> Participant:
        return Participant.objects.get(
            team=self.team, identifier=str(TELEGRAM_CHAT_ID), platform=ChannelPlatform.TELEGRAM
        )

    def notifications(self, title: str) -> int:
        return NotificationEvent.objects.filter(team=self.team, title=title).count()


def _human_messages(participant: Participant) -> list[ChatMessage]:
    return list(_messages(participant).filter(message_type=ChatMessageType.HUMAN))


def _ai_messages(participant: Participant) -> list[ChatMessage]:
    return list(_messages(participant).filter(message_type=ChatMessageType.AI))


def _messages(participant: Participant):
    return ChatMessage.objects.filter(chat__experiment_session__participant=participant).order_by("created_at")


@pytest.fixture(autouse=True)
def _eager_moderation_task():
    """Run Celery tasks in-process and keep `moderate_message` results in the result backend."""
    with (
        override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_STORE_EAGER_RESULT=True),
        mock.patch.object(moderate_message, "store_eager_result", True),
    ):
        yield


@pytest.fixture()
def make_chatbot(team_with_users, team_flag):
    """Builds a Telegram chatbot whose team has moderation and auto-block on.

    The bot's LLM and the Telegram Bot API client are faked. The feature flag is on unless `flag=False`.
    """
    llm = ScriptedLlm()

    def _make(*, nodes=None, history_type=PipelineChatHistoryTypes.GLOBAL, flag=True) -> Chatbot:
        team = team_with_users
        if flag:
            team_flag(flag_name=FLAG, team=team)
        select_moderation_provider(team=team, provider=ModerationProviderFactory.create(team=team))
        team.auto_block_enabled = True
        team.save()
        llm_provider = LlmProviderFactory.create(team=team)
        if nodes is None:
            llm_model = LlmProviderModelFactory.create(team=team)
            llm_node = llm_response_with_prompt_node(
                provider_id=str(llm_provider.id), provider_model_id=str(llm_model.id), history_type=history_type
            )
            nodes = [start_node(), llm_node, end_node()]
        experiment = ExperimentFactory.create(team=team)
        create_pipeline_model(nodes=nodes, pipeline=experiment.pipeline).save()
        ExperimentChannelFactory.create(
            team=team, experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "123:abc"}
        )
        return Chatbot(team=team, experiment=experiment, telegram_bot=telegram_bot.return_value, llm=llm)

    with (
        mock.patch.object(LlmProvider, "get_llm_service", return_value=FakeLlmService(llm=llm)),
        mock.patch("apps.channels.telegram_channel.TeleBot") as telegram_bot,
    ):
        yield _make


@pytest.fixture()
def chatbot(make_chatbot):
    return make_chatbot()


@pytest.mark.django_db()
def test_markup_is_recorded_as_a_rule_hit_without_a_strike(chatbot, httpx_mock):
    _moderation_returns(httpx_mock)

    sent = chatbot.send_telegram("<script>alert(1)</script>")

    assert sent == ["Bot answer 1"]
    participant = chatbot.telegram_participant()
    [human] = _human_messages(participant)
    assert human.moderation_detail.rules == ["markup"]
    assert human.moderation_status == ModerationStatus.PASSED
    assert participant.strikes == 0
    assert not DeniedParticipant.objects.filter(participant=participant).exists()


@pytest.mark.django_db()
def test_three_flagged_messages_block_the_participant(chatbot, httpx_mock):
    for expected_strikes in (1, 2, 3):
        _moderation_returns(httpx_mock, *STRIKE)

        sent = chatbot.send_telegram(OFFENSIVE_TEXT)

        participant = chatbot.telegram_participant()
        assert sent == [REPLACEMENT_REPLY]
        assert _ai_messages(participant)[-1].content == REPLACEMENT_REPLY
        assert _human_messages(participant)[-1].moderation_status == ModerationStatus.FLAGGED
        assert participant.strikes == expected_strikes

    denial = DeniedParticipant.objects.get(participant=participant)
    assert denial.source == DeniedParticipantSource.AUTO
    assert denial.chat_message == _human_messages(participant)[2]
    assert denial.experiment == chatbot.experiment
    assert denial.reason == ", ".join(sorted(STRIKE))
    assert chatbot.notifications("Participant blocked automatically") == 1
    bot_turns = len(chatbot.llm.bot_prompts())

    assert chatbot.send_telegram("hello again") == []
    assert len(chatbot.llm.bot_prompts()) == bot_turns
    assert len(_human_messages(participant)) == 3


@pytest.mark.django_db()
def test_blocked_widget_visitor_cannot_start_a_session_or_send(chatbot, admin, client):
    """A logged-in visitor who is not a team member, as on the site help widget, has a stable participant."""
    ExperimentChannelFactory.create(
        team=chatbot.team,
        experiment=chatbot.experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        required_auth_level=WidgetAuthLevel.EMBED_KEY,
        extra_data={"widget_token": WIDGET_TOKEN, "allowed_domains": ["example.com"]},
    )
    visitor = UserFactory.create()
    widget = APIClient()
    widget.force_login(visitor)
    widget_headers = {"HTTP_X_EMBED_KEY": WIDGET_TOKEN, "HTTP_ORIGIN": "https://example.com"}
    start_data = {"chatbot_id": str(chatbot.experiment.public_id), "participant_remote_id": visitor.email}
    started = widget.post(reverse("api:chat:start-session"), data=start_data, format="json", **widget_headers)
    assert started.status_code == 201, started.json()
    participant = Participant.objects.get(team=chatbot.team, user=visitor)
    client.force_login(admin)
    client.post(
        reverse("participants:block_participant", args=[chatbot.team.slug, participant.id]), data={"reason": "x"}
    )

    restarted = widget.post(reverse("api:chat:start-session"), data=start_data, format="json", **widget_headers)
    sent = widget.post(
        reverse("api:chat:send-message", kwargs={"session_id": started.json()["session_id"]}),
        data={"message": "hi"},
        format="json",
        HTTP_X_SESSION_TOKEN=started.json()["session_token"],
        **widget_headers,
    )

    for response in (restarted, sent):
        assert response.status_code == 403
        assert response.json() == BLOCKED_BODY
    assert chatbot.llm.bot_prompts() == []


@pytest.mark.django_db()
def test_blocked_participant_on_the_api_channel_gets_the_blocked_response(chatbot, admin, httpx_mock):
    api_client = ApiTestClient(admin, chatbot.team)
    url = reverse("channels:new_api_message", kwargs={"experiment_id": chatbot.experiment.public_id})
    _moderation_returns(httpx_mock)
    assert api_client.post(url, data=api_messages.text_message(), content_type="application/json").json() == {
        "response": "Bot answer 1",
        "attachments": [],
    }
    # The API caller is a team member, whom the participant page refuses to block, so the block is added here.
    participant = Participant.objects.get(team=chatbot.team, platform=ChannelPlatform.API)
    block_participant(participant=participant, source=DeniedParticipantSource.MANUAL)

    response = api_client.post(url, data=api_messages.text_message(), content_type="application/json")

    assert response.status_code == 403
    assert response.json() == BLOCKED_BODY
    assert len(chatbot.llm.bot_prompts()) == 1


@pytest.mark.django_db()
def test_only_non_strike_categories_flagged_passes(chatbot, httpx_mock):
    _moderation_returns(httpx_mock, "harassment")

    sent = chatbot.send_telegram("you are an idiot")

    assert sent == ["Bot answer 1"]
    participant = chatbot.telegram_participant()
    [human] = _human_messages(participant)
    assert human.moderation_status == ModerationStatus.PASSED
    assert human.moderation_detail.output.flagged_categories() == {"harassment"}
    assert _ai_messages(participant)[0].content == "Bot answer 1"
    assert participant.strikes == 0


@pytest.mark.django_db()
def test_team_member_in_app_is_not_struck_but_gets_a_strike_on_telegram(chatbot, httpx_mock, client):
    member = MembershipFactory.create(team=chatbot.team).user
    client.force_login(member)
    client.post(
        reverse(
            "chatbots:start_authed_web_session",
            args=[chatbot.team.slug, chatbot.experiment.id, chatbot.experiment.version_number],
        )
    )
    in_app = Participant.objects.get(team=chatbot.team, user=member)
    session = in_app.experimentsession_set.get()
    _moderation_returns(httpx_mock, *STRIKE)

    task_kwargs = {"experiment_session_id": session.id, "experiment_id": chatbot.experiment.id}
    result = get_response_for_webchat_task.apply(kwargs=task_kwargs | {"message_text": OFFENSIVE_TEXT}).get()

    assert result["response"] == REPLACEMENT_REPLY
    in_app.refresh_from_db()
    assert _human_messages(in_app)[0].moderation_status == ModerationStatus.FLAGGED
    assert in_app.strikes == 0
    assert not DeniedParticipant.objects.filter(participant=in_app).exists()

    _moderation_returns(httpx_mock, *STRIKE)
    assert chatbot.send_telegram(OFFENSIVE_TEXT) == [REPLACEMENT_REPLY]
    assert chatbot.telegram_participant().strikes == 1


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("provider_failure", "expected_requests", "expected_reason"),
    [
        pytest.param({"status_code": 429}, 1, ModerationSkipReason.RATE_LIMITED, id="rate_limited"),
        pytest.param({"status_code": 500}, 3, ModerationSkipReason.ERROR, id="server_error_after_retries"),
        pytest.param(
            {"exception": httpx.ReadTimeout("timed out")}, 3, ModerationSkipReason.ERROR, id="request_timeout"
        ),
    ],
)
def test_provider_failure_skips_moderation(chatbot, httpx_mock, provider_failure, expected_requests, expected_reason):
    if "exception" in provider_failure:
        httpx_mock.add_exception(exception=provider_failure["exception"], url=MODERATIONS_URL, is_reusable=True)
    else:
        httpx_mock.add_response(
            url=MODERATIONS_URL,
            json={"error": {"message": "failed", "type": "error"}},
            is_reusable=True,
            **provider_failure,
        )

    sent = chatbot.send_telegram(OFFENSIVE_TEXT)

    _assert_skipped(chatbot, sent=sent, expected_reason=expected_reason)
    assert len(httpx_mock.get_requests(url=MODERATIONS_URL)) == expected_requests


@pytest.mark.django_db()
def test_no_moderation_result_in_time_skips_moderation(chatbot, httpx_mock):
    _moderation_returns(httpx_mock, *STRIKE)

    with (
        mock.patch.object(moderate_message, "store_eager_result", False),
        mock.patch("apps.moderation.services.MODERATION_WAIT_TIMEOUT", 0.3),
    ):
        sent = chatbot.send_telegram(OFFENSIVE_TEXT)

    _assert_skipped(chatbot, sent=sent, expected_reason=ModerationSkipReason.TIMEOUT)


def _assert_skipped(chatbot: Chatbot, *, sent: list[str], expected_reason: ModerationSkipReason) -> None:
    assert sent == ["Bot answer 1"]
    participant = chatbot.telegram_participant()
    [human] = _human_messages(participant)
    assert human.moderation_status == ModerationStatus.SKIPPED
    assert human.moderation_detail.skip_reason == expected_reason
    assert chatbot.notifications("Moderation skipped") == 1
    assert participant.strikes == 0


@pytest.mark.django_db()
def test_unblocked_participant_is_answered_again(chatbot, httpx_mock, admin, client):
    for _ in range(3):
        _moderation_returns(httpx_mock, *STRIKE)
        chatbot.send_telegram(OFFENSIVE_TEXT)
    participant = chatbot.telegram_participant()
    denial = DeniedParticipant.objects.get(participant=participant)
    client.force_login(admin)

    with enable_audit():
        client.post(reverse("participants:unblock_participant", args=[chatbot.team.slug, participant.id]))

    assert not DeniedParticipant.objects.filter(participant=participant).exists()
    participant.refresh_from_db()
    assert participant.strikes == 0
    assert AuditEvent.objects.by_model(DeniedParticipant).get(object_pk=denial.pk).is_delete
    _moderation_returns(httpx_mock)
    assert chatbot.send_telegram("hello again") == ["Bot answer 4"]


@pytest.mark.django_db()
def test_code_node_blocks_the_participant(make_chatbot, httpx_mock, admin, client):
    code = 'def main(input, **kwargs):\n    block_participant("test")\n    abort_with_message("Goodbye")\n'
    chatbot = make_chatbot(nodes=[start_node(), code_node(code=code), end_node()])
    _moderation_returns(httpx_mock)

    # EventBot rewrites the abort message, so the reply is the fake LLM's EventBot answer.
    assert chatbot.send_telegram("hello") == [REPLACEMENT_REPLY]

    denial = DeniedParticipant.objects.get(participant=chatbot.telegram_participant())
    assert (denial.source, denial.reason, denial.experiment) == (
        DeniedParticipantSource.CODE_NODE,
        "test",
        chatbot.experiment,
    )
    client.force_login(admin)
    table = client.get(reverse("moderation:denied_participants_table", args=[chatbot.team.slug]))
    assert "Pipeline" in table.content.decode()
    assert chatbot.send_telegram("hello again") == []


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "history_type",
    [
        pytest.param(PipelineChatHistoryTypes.GLOBAL, id="global_history"),
        pytest.param(
            PipelineChatHistoryTypes.NODE,
            id="node_history",
            marks=pytest.mark.xfail(
                strict=True, reason="node history drops the flagged turn but never records the replacement reply"
            ),
        ),
    ],
)
def test_flagged_turn_is_left_out_of_the_next_prompt(make_chatbot, httpx_mock, history_type):
    chatbot = make_chatbot(history_type=history_type)
    _moderation_returns(httpx_mock, *STRIKE)
    chatbot.send_telegram(OFFENSIVE_TEXT)
    _moderation_returns(httpx_mock)

    assert chatbot.send_telegram("What is the weather?") == ["Bot answer 2"]

    prompt_texts = [message.text for message in chatbot.llm.bot_prompts()[-1]]
    assert OFFENSIVE_TEXT not in prompt_texts
    assert "Bot answer 1" not in prompt_texts
    assert REPLACEMENT_REPLY in prompt_texts
    assert prompt_texts[-1] == "What is the weather?"


@pytest.mark.django_db()
def test_flag_off_skips_moderation_and_enforcement(make_chatbot, httpx_mock):
    chatbot = make_chatbot(flag=False)
    participant = ParticipantFactory.create(
        team=chatbot.team, identifier=str(TELEGRAM_CHAT_ID), platform=ChannelPlatform.TELEGRAM
    )
    block_participant(participant=participant, source=DeniedParticipantSource.MANUAL)

    sent = [chatbot.send_telegram(OFFENSIVE_TEXT) for _ in range(3)]

    assert sent == [["Bot answer 1"], ["Bot answer 2"], ["Bot answer 3"]]
    assert httpx_mock.get_requests() == []
    assert [m.moderation_status for m in _human_messages(participant)] == [None, None, None]
    participant.refresh_from_db()
    assert participant.strikes == 0

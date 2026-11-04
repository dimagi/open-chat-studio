"""Fixtures for the trigger endpoints (#4144)."""

import datetime
from dataclasses import dataclass
from typing import Any

import pytest

from apps.utils.factories.events import ScheduledTriggerFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

# Two days out, so the date is still in the future in every timezone the tests schedule in.
FUTURE_DATE = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
LOG_ACTION = {"action_type": "log", "params": {}}


@dataclass(frozen=True)
class TriggerKind:
    path: str
    factory: Any
    #: The chatbot's reverse accessor for this kind.
    related_name: str
    #: The trigger's own fields, without the action.
    fields: dict

    @property
    def model(self):
        return self.factory._meta.model

    def body(self, action=None, **fields) -> dict:
        return {**self.fields, **fields, "action": action or LOG_ACTION}

    def list_url(self, chatbot) -> str:
        return f"/api/v2/chatbots/{chatbot.public_id}/{self.path}/"

    def detail_url(self, chatbot, trigger_id: int) -> str:
        return f"{self.list_url(chatbot)}{trigger_id}/"


STATIC = TriggerKind(
    path="static-triggers",
    factory=StaticTriggerFactory,
    related_name="static_triggers",
    fields={"type": "conversation_start"},
)
TIMEOUT = TriggerKind(
    path="timeout-triggers",
    factory=TimeoutTriggerFactory,
    related_name="timeout_triggers",
    fields={"delay": 3600, "total_num_triggers": 2, "trigger_from_first_message": True},
)
SCHEDULED = TriggerKind(
    path="scheduled-triggers",
    factory=ScheduledTriggerFactory,
    related_name="scheduled_triggers",
    fields={"trigger_date": FUTURE_DATE, "trigger_time": "09:30:00", "timezone": "Africa/Johannesburg"},
)
KINDS = [
    pytest.param(STATIC, id="static"),
    pytest.param(TIMEOUT, id="timeout"),
    pytest.param(SCHEDULED, id="scheduled"),
]


@pytest.fixture()
def chatbot(db):
    return ChatbotFactory.create(team=TeamWithUsersFactory.create())


@pytest.fixture()
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)

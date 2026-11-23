"""The Decision Node answered by OpenAI's Decisions API (#5025)."""

import json
import time
from unittest import mock

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.cost_tracking.models import Confidence, ServiceKind
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import DecisionNode, RouterNode
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tests.utils import build_decision_node, decision_routing_runnable
from apps.service_providers.tracing.metrics import MetricsCollector

URL = "https://api.openai.com/v1/decisions"

INTENT = {
    "key": "intent",
    "type": "choice",
    "instructions": "What does the participant want?",
    "options": [{"name": "billing", "description": "Payments and invoices"}, {"name": "support"}],
    "threshold": 0.6,
}
FRUSTRATION = {"key": "frustration", "type": "score", "instructions": "How frustrated?", "levels": ["Calm", "Angry"]}
REFUND = {"key": "wants_refund", "type": "noul", "instructions": "The participant asks for a refund."}

USAGE = {"input_tokens": 215, "output_tokens": 0, "total_tokens": 215}


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.decision_api.time.sleep"):
        yield


def _choice(name, choice, confidence):
    return {
        "name": name,
        "type": "choice",
        "choice": choice,
        "confidence": confidence,
        "probabilities": [{"value": choice, "probability": confidence}],
    }


def _response(*answers) -> dict:
    return {"model": "gpt-6-luna", "answers": list(answers), "usage": USAGE}


def _state(session) -> PipelineState:
    return PipelineState(
        messages=["I was charged twice"],
        experiment_session=session,
        last_node_input="I was charged twice",
        temp_state={"user_input": "I was charged twice"},
        participant_data={"plan": "pro"},
        session_state={},
    )


def _node(ids, session=None, decisions=None) -> DecisionNode:
    return build_decision_node(ids, decisions if decisions is not None else [INTENT, FRUSTRATION, REFUND], session)


def _run(node: DecisionNode, session, config=None) -> PipelineState:
    node._config = config or {}
    state = _state(session)
    return node._process(state, NodeContext(state))


def _temp_state(output: PipelineState) -> dict:
    return {key: value for key, value in output["temp_state"].items() if key != "outputs"}


class TestDecisionNode:
    def test_asks_every_decision_in_one_call(self, httpx_mock, openai_decision_ids, session):
        httpx_mock.add_response(url=URL, json=_response())

        _run(_node(openai_decision_ids, session), session)

        body = json.loads(httpx_mock.get_request().content)
        assert body["model"] == "gpt-6-luna"
        assert body["questions"] == [
            {
                "type": "choice",
                "name": "intent",
                "instructions": "What does the participant want?",
                "choices": [{"value": "billing", "description": "Payments and invoices"}, {"value": "support"}],
            },
            {
                "type": "score",
                "name": "frustration",
                "instructions": "How frustrated?",
                "levels": [{"label": "Calm"}, {"label": "Angry"}],
            },
            {"type": "predicate", "name": "wants_refund", "instructions": "The participant asks for a refund."},
        ]
        state = json.loads(body["input"])
        assert state["participant_data"] == {"plan": "pro"}
        assert state["temp_state"] == {"user_input": "I was charged twice"}

    def test_writes_each_answer_and_no_yes_no_confidence(self, httpx_mock, openai_decision_ids, session):
        score = {"name": "frustration", "type": "score", "score": 0.8, "confidence": 0.7, "probabilities": []}
        refund = {"name": "wants_refund", "type": "predicate", "probability": 0.2}
        httpx_mock.add_response(url=URL, json=_response(_choice("intent", "billing", 0.9), score, refund))

        output = _run(_node(openai_decision_ids, session), session)

        assert _temp_state(output) == {
            "intent": "billing",
            "intent_confidence": 0.9,
            "frustration": 0.8,
            "frustration_confidence": 0.7,
            "wants_refund": 0.2,
        }

    def test_a_refused_decision_writes_null_and_the_others_are_written(self, httpx_mock, openai_decision_ids, session):
        refusal = {"name": "intent", "type": "refusal"}
        refund = {"name": "wants_refund", "type": "predicate", "probability": 0.7}
        httpx_mock.add_response(url=URL, json=_response(refusal, refund))

        output = _run(_node(openai_decision_ids, session, decisions=[INTENT, REFUND]), session)

        assert _temp_state(output) == {"intent": None, "intent_confidence": None, "wants_refund": 0.7}

    def test_usage_is_reported_under_openai(self, httpx_mock, openai_decision_ids, session):
        httpx_mock.add_response(url=URL, json=_response())
        collector = MetricsCollector(start_time=time.time())

        _run(_node(openai_decision_ids, session), session, config={"callbacks": [collector]})

        events = {(e.provider_type, e.model_name, e.service_kind): e.quantity for e in collector.iter_cost_events()}
        assert events == {("openai", "gpt-6-luna", ServiceKind.LLM_INPUT): 215}
        assert {e.confidence for e in collector.iter_cost_events()} == {Confidence.EXACT}

    @pytest.mark.parametrize(
        ("decisions", "message"),
        [
            pytest.param([{**INTENT, "options": [{"name": "billing"}]}], "needs 2 to 255 options", id="one-option"),
            pytest.param(
                [{**FRUSTRATION, "levels": [f"level {i}" for i in range(11)]}], "needs 2 to 10 levels", id="11-levels"
            ),
            pytest.param([{**REFUND, "key": f"q{i}"} for i in range(401)], "at most 400 decisions", id="too-many"),
        ],
    )
    def test_rejects_what_openai_does_not_take(self, openai_decision_ids, decisions, message):
        with pytest.raises(PydanticValidationError, match=message):
            _node(openai_decision_ids, decisions=decisions)


@pytest.mark.django_db()
def test_a_router_on_gpt_6_luna_keeps_routing_with_the_chat_model(openai_decision_ids, session):
    """gpt-6-luna also writes text, so a router on it is a chat router, as it was before it made decisions."""
    router = RouterNode(
        node_id="router",
        django_node=None,
        name="router",
        keywords=["billing", "support"],
        **openai_decision_ids,
    )
    router._repo = ORMRepository(session=session)
    router._config = {}

    with mock.patch.object(RouterNode, "_route_with_agent", return_value="SUPPORT") as route_with_agent:
        keyword, _ = router._process_conditional(NodeContext(_state(session)))

    assert keyword == "SUPPORT"
    route_with_agent.assert_called_once()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        pytest.param(("support", 0.9), "Support: I was charged twice", id="routes-on-the-choice"),
        pytest.param(("support", 0.3), "Billing: I was charged twice", id="below-threshold-takes-the-default"),
    ],
)
def test_a_static_router_routes_on_openais_decision(httpx_mock, openai_decision_ids, session, intent, expected):
    httpx_mock.add_response(url=URL, json=_response(_choice("intent", *intent)))
    runnable = decision_routing_runnable(openai_decision_ids, [INTENT], "intent", ["billing", "support"])

    output = runnable.invoke(
        PipelineState(messages=["I was charged twice"], experiment_session=session),
        config={"configurable": {"repo": ORMRepository(session=session)}},
    )

    assert output["messages"][-1] == expected

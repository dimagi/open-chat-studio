"""The LLM Router and the Decision Node answered by Google's Decisions API (#5042)."""

import json
import time
from unittest import mock

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.chat.exceptions import ProviderConfigurationError
from apps.cost_tracking.models import Confidence, ServiceKind
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import DecisionNode, RouterNode
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tests.utils import (
    build_decision_node,
    create_runnable,
    decision_node,
    decision_routing_runnable,
    end_node,
    start_node,
)
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory

URL = "https://generativelanguage.googleapis.com/v1beta/decisions"

INTENT = {
    "key": "intent",
    "type": "choice",
    "instructions": "What does the participant want?",
    "options": [{"name": "billing", "description": "Payments and invoices"}, {"name": "support"}],
    "threshold": 0.6,
}
FRUSTRATION = {"key": "frustration", "type": "score", "instructions": "How frustrated?", "levels": ["Calm", "Angry"]}
REFUND = {"key": "wants_refund", "type": "noul", "instructions": "The participant asks for a refund."}

USAGE = {"total_tokens": 215, "total_input_tokens": 215, "total_output_tokens": 0}


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.decision_api.time.sleep"):
        yield


def _choice(choice, confidence):
    return {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {choice: confidence}}


def _sent(httpx_mock) -> dict:
    return json.loads(httpx_mock.get_request().content)


def _state(session) -> PipelineState:
    return PipelineState(
        messages=["I was charged twice"],
        experiment_session=session,
        last_node_input="I was charged twice",
        temp_state={"user_input": "I was charged twice"},
        participant_data={"plan": "pro"},
        session_state={},
    )


def _router(ids, session=None, keywords=("billing", "support"), **params) -> RouterNode:
    router = RouterNode(
        node_id="router",
        django_node=None,
        name="router",
        keywords=list(keywords),
        route_descriptions={"billing": "Payments and invoices"},
        **ids,
        **params,
    )
    router._repo = ORMRepository(session=session)
    return router


def _route(router: RouterNode, session, config=None):
    router._config = config or {}
    return router._process_conditional(NodeContext(_state(session)))


def _node(ids, session=None, decisions=None) -> DecisionNode:
    return build_decision_node(ids, decisions if decisions is not None else [INTENT, FRUSTRATION, REFUND], session)


def _run(node: DecisionNode, session, config=None) -> PipelineState:
    node._config = config or {}
    state = _state(session)
    return node._process(state, NodeContext(state))


def _cost_events(collector: MetricsCollector) -> dict:
    return {
        (e.provider_type, e.model_name, e.service_kind, e.confidence): e.quantity for e in collector.iter_cost_events()
    }


class TestRouter:
    def test_asks_google_one_choice_over_the_keywords(self, httpx_mock, google_decision_ids, session):
        httpx_mock.add_response(url=URL, json={"answers": {"route": _choice("SUPPORT", 0.9)}, "usage": USAGE})

        assert _route(_router(google_decision_ids, session), session) == ("SUPPORT", False)

        body = _sent(httpx_mock)
        assert body["model"] == "gemini-decision-1"
        assert body["questions"]["route"]["criteria"] == {"BILLING": "Payments and invoices", "SUPPORT": ""}
        assert "BILLING" in body["questions"]["route"]["instructions"]
        (block,) = body["input"]
        assert json.loads(block["text"])["input"] == "I was charged twice"

    @pytest.mark.parametrize(
        ("status_code", "error"),
        [
            pytest.param(400, {"code": "safety", "message": "Blocked"}, id="safety-block"),
            pytest.param(503, {"code": 503, "message": "Overloaded", "status": "UNAVAILABLE"}, id="unavailable"),
        ],
    )
    def test_a_failed_call_takes_the_default_route(self, httpx_mock, google_decision_ids, session, status_code, error):
        httpx_mock.add_response(url=URL, status_code=status_code, json={"error": error}, is_reusable=True)

        assert _route(_router(google_decision_ids, session), session) == ("BILLING", True)

    def test_a_low_confidence_takes_the_default_route(self, httpx_mock, google_decision_ids, session):
        httpx_mock.add_response(url=URL, json={"answers": {"route": _choice("SUPPORT", 0.4)}, "usage": USAGE})

        router = _router(google_decision_ids, session, confidence_threshold=0.5)

        assert _route(router, session) == ("BILLING", True)

    def test_a_rejected_key_is_a_configuration_error(self, httpx_mock, google_decision_ids, session):
        error = {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}
        error["details"] = [{"reason": "API_KEY_INVALID"}]
        httpx_mock.add_response(url=URL, status_code=400, json={"error": error})

        with pytest.raises(ProviderConfigurationError, match="rejected the credentials"):
            _route(_router(google_decision_ids, session), session)

    def test_usage_is_input_only_under_google(self, httpx_mock, google_decision_ids, session):
        httpx_mock.add_response(url=URL, json={"answers": {"route": _choice("BILLING", 0.9)}, "usage": USAGE})
        collector = MetricsCollector(start_time=time.time())

        _route(_router(google_decision_ids, session), session, config={"callbacks": [collector]})

        assert _cost_events(collector) == {
            ("google", "gemini-decision-1", ServiceKind.LLM_INPUT, Confidence.EXACT): 215,
        }
        assert collector.get_metrics().n_turns == 1

    def test_a_router_with_one_route_is_rejected(self, google_decision_ids):
        with pytest.raises(PydanticValidationError, match="The router needs 2 to 255 routes") as excinfo:
            _router(google_decision_ids, keywords=["billing"])

        assert excinfo.value.errors()[0]["ctx"]["field"] == "keywords"

    def test_a_route_name_longer_than_google_takes_is_rejected(self, google_decision_ids):
        with pytest.raises(PydanticValidationError, match="of at most 128 characters"):
            _router(google_decision_ids, keywords=["billing", "s" * 129])

    def test_a_jev_router_with_one_route_is_accepted(self, jev_ids):
        assert _router(jev_ids, keywords=["billing"]).keywords == ["BILLING"]

    def test_a_google_chat_model_router_is_not_held_to_decision_limits(self, google_decision_ids):
        chat_model = LlmProviderModelFactory.create(type="google", name="gemini-3.8-flash")
        ids = {**google_decision_ids, "llm_provider_model_id": chat_model.id}

        assert _router(ids, keywords=["billing"]).keywords == ["BILLING"]


class TestDecisionNode:
    def test_asks_every_decision_in_one_call(self, httpx_mock, google_decision_ids, session):
        httpx_mock.add_response(url=URL, json={"answers": {}, "usage": USAGE})

        _run(_node(google_decision_ids, session), session)

        body = _sent(httpx_mock)
        assert body["questions"] == {
            "intent": {
                "type": "choice",
                "instructions": "What does the participant want?",
                "criteria": {"billing": "Payments and invoices", "support": ""},
            },
            "frustration": {"type": "score", "instructions": "How frustrated?", "criteria": ["Calm", "Angry"]},
            "wants_refund": {"type": "noul", "instructions": "The participant asks for a refund."},
        }
        state = json.loads(body["input"][0]["text"])
        assert state["participant_data"] == {"plan": "pro"}
        assert state["temp_state"] == {"user_input": "I was charged twice"}

    def test_writes_each_answer_with_its_confidence(self, httpx_mock, google_decision_ids, session):
        answers = {
            "intent": _choice("billing", 0.9),
            "frustration": {"type": "score", "score": 0.8, "confidence": 0.7, "probabilities": {}},
            "wants_refund": {"type": "noul", "noul": 0.2, "confidence": 0.8},
        }
        httpx_mock.add_response(url=URL, json={"answers": answers, "usage": USAGE})

        output = _run(_node(google_decision_ids, session), session)

        temp_state = {key: value for key, value in output["temp_state"].items() if key != "outputs"}
        assert temp_state == {
            "intent": "billing",
            "intent_confidence": 0.9,
            "frustration": 0.8,
            "frustration_confidence": 0.7,
            "wants_refund": 0.2,
            "wants_refund_confidence": 0.8,
        }
        assert output["messages"] == ["I was charged twice"]

    def test_a_safety_block_writes_null_for_every_decision(self, httpx_mock, google_decision_ids, session, caplog):
        httpx_mock.add_response(url=URL, status_code=400, json={"error": {"code": "safety", "message": "Blocked"}})

        temp_state = _run(_node(google_decision_ids, session, decisions=[INTENT, REFUND]), session)["temp_state"]

        assert (temp_state["intent"], temp_state["intent_confidence"], temp_state["wants_refund"]) == (None, None, None)
        assert "Google returned 400 (safety)" in caplog.text

    @pytest.mark.parametrize(
        "response",
        [
            pytest.param({"json": {"answers": {}, "usage": USAGE}}, id="no-answer"),
            pytest.param({"status_code": 500, "json": {"error": {"message": "boom"}}}, id="failed-call"),
        ],
    )
    def test_a_noul_without_an_answer_writes_a_null_confidence(
        self, httpx_mock, google_decision_ids, session, response
    ):
        httpx_mock.add_response(url=URL, **response)

        temp_state = _run(_node(google_decision_ids, session, decisions=[REFUND]), session)["temp_state"]

        assert {key: temp_state[key] for key in ("wants_refund", "wants_refund_confidence")} == {
            "wants_refund": None,
            "wants_refund_confidence": None,
        }

    def test_an_unanswered_noul_replaces_an_earlier_nodes_confidence(self, httpx_mock, google_decision_ids, session):
        """Two nodes writing one key: the second's missing answer must not leave the first's confidence behind."""
        answered = {"wants_refund": {"type": "noul", "noul": 0.97, "confidence": 0.95}}
        httpx_mock.add_response(url=URL, json={"answers": answered, "usage": USAGE})
        httpx_mock.add_response(url=URL, json={"answers": {}, "usage": USAGE})
        start, end = start_node(), end_node()
        first = decision_node(google_decision_ids, [REFUND], name="first")
        second = decision_node(google_decision_ids, [REFUND], name="second")
        edges = [
            {"id": "start -> first", "source": start["id"], "target": first["id"]},
            {"id": "first -> second", "source": first["id"], "target": second["id"]},
            {"id": "second -> end", "source": second["id"], "target": end["id"]},
        ]
        runnable = create_runnable(PipelineFactory.create(), [start, first, second, end], edges)

        output = runnable.invoke(
            PipelineState(messages=["I was charged twice"], experiment_session=session),
            config={"configurable": {"repo": ORMRepository(session=session)}},
        )

        assert (output["temp_state"]["wants_refund"], output["temp_state"]["wants_refund_confidence"]) == (None, None)

    def test_usage_is_input_only_under_google(self, httpx_mock, google_decision_ids, session):
        httpx_mock.add_response(url=URL, json={"answers": {}, "usage": USAGE})
        collector = MetricsCollector(start_time=time.time())

        _run(_node(google_decision_ids, session), session, config={"callbacks": [collector]})

        assert _cost_events(collector) == {
            ("google", "gemini-decision-1", ServiceKind.LLM_INPUT, Confidence.EXACT): 215,
        }

    @pytest.mark.parametrize(
        ("decisions", "message"),
        [
            pytest.param([{**INTENT, "options": [{"name": "billing"}]}], "needs 2 to 255 options", id="one-option"),
            pytest.param([{**FRUSTRATION, "levels": ["Calm"]}], "needs 2 to 255 levels", id="one-level"),
            pytest.param([{**REFUND, "key": "k" * 65}], "is not a key this model accepts", id="key-too-long"),
            pytest.param([{**REFUND, "key": "intención"}], "is not a key this model accepts", id="key-not-ascii"),
            pytest.param(
                [{**INTENT, "options": [{"name": "billing"}, {"name": "s" * 129}]}],
                "of at most 128 characters",
                id="option-name-too-long",
            ),
            pytest.param([{**REFUND, "instructions": " "}], "needs instructions", id="no-instructions"),
            pytest.param([{**REFUND, "key": f"q{i}"} for i in range(1001)], "at most 1000 decisions", id="too-many"),
        ],
    )
    def test_rejects_what_google_does_not_take(self, google_decision_ids, decisions, message):
        with pytest.raises(PydanticValidationError, match=message) as excinfo:
            _node(google_decision_ids, decisions=decisions)

        assert excinfo.value.errors()[0]["ctx"]["field"] == "decisions"

    @pytest.mark.parametrize(
        "decision",
        [
            pytest.param({**INTENT, "options": [{"name": "billing"}]}, id="one-option"),
            pytest.param({**REFUND, "instructions": ""}, id="no-instructions"),
            pytest.param({**REFUND, "key": "intención"}, id="non-ascii-key"),
        ],
    )
    def test_jev_still_takes_what_google_does_not(self, jev_ids, decision):
        assert len(_node(jev_ids, decisions=[decision]).decisions) == 1


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        pytest.param(("support", 0.9), "Support: I was charged twice", id="routes-on-the-choice"),
        pytest.param(("support", 0.3), "Billing: I was charged twice", id="below-threshold-takes-the-default"),
    ],
)
def test_a_static_router_routes_on_googles_decision(httpx_mock, google_decision_ids, session, intent, expected):
    httpx_mock.add_response(url=URL, json={"answers": {"intent": _choice(*intent)}, "usage": USAGE})
    runnable = decision_routing_runnable(google_decision_ids, [INTENT], "intent", ["billing", "support"])

    output = runnable.invoke(
        PipelineState(messages=["I was charged twice"], experiment_session=session),
        config={"configurable": {"repo": ORMRepository(session=session)}},
    )

    assert output["messages"][-1] == expected

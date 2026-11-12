"""The Decision Node: several Jev questions in one call, answers written to the temporary state (#4631)."""

import json
import time

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.chat.exceptions import ProviderConfigurationError
from apps.cost_tracking.models import Confidence, ServiceKind
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import DecisionNode, StaticRouterNode
from apps.pipelines.repository import ORMRepository
from apps.pipelines.tests.utils import (
    create_runnable,
    decision_node,
    end_node,
    render_template_node,
    start_node,
    state_key_router_node,
)
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory

URL = "https://api.typesafe.ai/v1/systemone"

INTENT = {
    "key": "intent",
    "type": "choice",
    "instructions": "What does the participant want?",
    "options": [{"name": "billing", "description": "Payments and invoices"}, {"name": "support", "description": ""}],
    "threshold": 0.6,
}
FRUSTRATION = {"key": "frustration", "type": "score", "levels": ["Calm", "Annoyed", "Angry"]}
REFUND = {"key": "wants_refund", "type": "noul", "instructions": "The participant asks for a refund."}


def _answers(intent=("billing", 0.9), frustration=(1.7, 0.55), refund=0.98):
    choice, confidence = intent
    score, score_confidence = frustration
    return {
        "model": "jev-1.14.0",
        "answers": {
            "intent": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {}},
            "frustration": {"type": "score", "score": score, "confidence": score_confidence, "probabilities": {}},
            "wants_refund": {"type": "noul", "noul": refund},
        },
        "usage": {"input_tokens": 300, "output_tokens": 30},
    }


def _node(jev_ids, session=None, decisions=None, **params) -> DecisionNode:
    node = DecisionNode(
        node_id="decide",
        django_node=None,
        name="decide",
        decisions=decisions if decisions is not None else [INTENT, FRUSTRATION, REFUND],
        **jev_ids,
        **params,
    )
    node._repo = ORMRepository(session=session)
    return node


def _run(node: DecisionNode, session, config=None) -> PipelineState:
    node._config = config or {}
    state = PipelineState(
        messages=["I was charged twice"],
        experiment_session=session,
        last_node_input="I was charged twice",
        temp_state={"user_input": "I was charged twice"},
        participant_data={"plan": "pro"},
        session_state={},
    )
    return node._process(state, NodeContext(state))


def _sent(httpx_mock) -> dict:
    return json.loads(httpx_mock.get_request().content)


class TestValidation:
    @pytest.mark.parametrize(
        ("decisions", "message"),
        [
            pytest.param([], "Add at least one decision", id="no-decisions"),
            pytest.param([{**REFUND, "key": "2fast"}], "is not a valid key", id="key-not-an-identifier"),
            pytest.param([{**REFUND, "key": "outputs"}], "is reserved", id="reserved-key"),
            pytest.param([REFUND, {**FRUSTRATION, "key": "wants_refund"}], "writes to .wants_refund.", id="same-key"),
            pytest.param(
                [INTENT, {**REFUND, "key": "intent_confidence"}], "where intent writes its confidence", id="confidence"
            ),
            pytest.param([{**INTENT, "options": []}], "needs 1 to 255 options", id="choice-without-options"),
            pytest.param(
                [{**INTENT, "options": [{"name": str(i)} for i in range(256)]}],
                "needs 1 to 255 options",
                id="too-many-options",
            ),
            pytest.param([{**INTENT, "options": [{"name": " "}]}], "needs a name", id="blank-option"),
            pytest.param(
                [{**INTENT, "options": [{"name": "Yes"}, {"name": "YES"}]}], "more than once", id="repeated-option"
            ),
            pytest.param([{**FRUSTRATION, "levels": []}], "needs 1 to 10 levels", id="score-without-levels"),
            pytest.param([{**FRUSTRATION, "levels": ["x"] * 11}], "needs 1 to 10 levels", id="too-many-levels"),
            pytest.param([{**FRUSTRATION, "levels": ["Calm", ""]}], "describe every level", id="blank-level"),
            pytest.param([{**REFUND, "threshold": 0.5}], "threshold does not apply", id="noul-threshold"),
            pytest.param([{**FRUSTRATION, "options": [{"name": "a"}]}], "options does not apply", id="stray-options"),
            pytest.param([{**INTENT, "threshold": 1.5}], "less than or equal to 1", id="threshold-above-one"),
            pytest.param([{**REFUND, "type": "boolean"}], "Input should be", id="unknown-type"),
        ],
    )
    def test_rejects(self, jev_ids, decisions, message):
        with pytest.raises(PydanticValidationError, match=message):
            _node(jev_ids, decisions=decisions)

    def test_accepts_one_decision_of_each_type(self, jev_ids):
        node = _node(jev_ids)

        assert [decision.key for decision in node.decisions] == ["intent", "frustration", "wants_refund"]

    def test_option_names_are_stored_without_surrounding_spaces(self, jev_ids):
        decision = {**INTENT, "options": [{"name": " billing "}, {"name": "support"}]}

        node = _node(jev_ids, decisions=[decision])

        assert [option.name for option in node.decisions[0].options] == ["billing", "support"]

    def test_rejects_a_model_that_is_not_jev(self, db):
        provider = LlmProviderFactory.create()
        model = LlmProviderModelFactory.create(team=provider.team)
        ids = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}

        with pytest.raises(PydanticValidationError, match="The Decision Node needs a TypeSafe Jev model"):
            _node(ids)

    def test_the_model_picker_offers_only_jev(self):
        schema = DecisionNode.model_json_schema()["properties"]["llm_provider_id"]

        assert schema["ui:includedProviderTypes"] == ["jev"]
        assert "ui:excludedProviderTypes" not in schema


class TestRequest:
    def test_asks_every_decision_in_one_call(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())

        _run(_node(jev_ids, session), session)

        assert len(httpx_mock.get_requests()) == 1
        assert _sent(httpx_mock)["questions"] == {
            "intent": {
                "type": "choice",
                "instructions": "What does the participant want?",
                "criteria": {"billing": "Payments and invoices", "support": None},
            },
            "frustration": {"type": "score", "instructions": None, "criteria": ["Calm", "Annoyed", "Angry"]},
            "wants_refund": {"type": "noul", "instructions": "The participant asks for a refund.", "criteria": None},
        }

    def test_a_noul_with_descriptions_sends_them_as_criteria(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())
        decision = {**REFUND, "true_description": "Asks for money back"}

        _run(_node(jev_ids, session, decisions=[decision]), session)

        criteria = _sent(httpx_mock)["questions"]["wants_refund"]["criteria"]
        assert criteria == {"true": "Asks for money back", "false": None}

    def test_state_carries_the_conversation(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())

        _run(_node(jev_ids, session), session)

        state = _sent(httpx_mock)["state"]
        assert state["input"] == "I was charged twice"
        assert state["participant_data"] == {"plan": "pro"}
        assert state["temp_state"] == {"user_input": "I was charged twice"}


class TestResults:
    def test_writes_each_answer_and_passes_the_input_through(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())

        output = _run(_node(jev_ids, session), session)

        assert output["messages"] == ["I was charged twice"]
        temp_state = {key: value for key, value in output["temp_state"].items() if key != "outputs"}
        assert temp_state == {
            "intent": "billing",
            "intent_confidence": 0.9,
            "frustration": 1.7,
            "frustration_confidence": 0.55,
            "wants_refund": 0.98,
        }

    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            pytest.param(("support", 0.6), "support", id="at-the-threshold"),
            pytest.param(("support", 0.59), None, id="below-the-threshold"),
            pytest.param(("BILLING", 0.9), "billing", id="option-named-in-another-case"),
            pytest.param(("refunds", 0.9), None, id="not-an-option"),
        ],
    )
    def test_choice(self, httpx_mock, jev_ids, session, intent, expected):
        httpx_mock.add_response(url=URL, json=_answers(intent=intent))

        temp_state = _run(_node(jev_ids, session), session)["temp_state"]

        assert (temp_state["intent"], temp_state["intent_confidence"]) == (expected, intent[1])

    def test_a_score_below_its_threshold_is_null(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers(frustration=(2.0, 0.4)))
        decisions = [{**FRUSTRATION, "threshold": 0.5}]

        temp_state = _run(_node(jev_ids, session, decisions=decisions), session)["temp_state"]

        assert (temp_state["frustration"], temp_state["frustration_confidence"]) == (None, 0.4)

    def test_a_missing_answer_is_null_and_the_rest_are_written(self, httpx_mock, jev_ids, session, caplog):
        answers = _answers()
        del answers["answers"]["frustration"]
        httpx_mock.add_response(url=URL, json=answers)

        temp_state = _run(_node(jev_ids, session), session)["temp_state"]

        assert (temp_state["frustration"], temp_state["frustration_confidence"]) == (None, None)
        assert temp_state["intent"] == "billing"
        assert "no usable answer for frustration" in caplog.text

    def test_a_failed_call_writes_null_for_every_decision(self, httpx_mock, jev_ids, session, caplog):
        httpx_mock.add_response(url=URL, status_code=500, json={"detail": "boom"})

        output = _run(_node(jev_ids, session), session)

        temp_state = {key: value for key, value in output["temp_state"].items() if key != "outputs"}
        assert temp_state == {
            "intent": None,
            "intent_confidence": None,
            "frustration": None,
            "frustration_confidence": None,
            "wants_refund": None,
        }
        assert output["messages"] == ["I was charged twice"]
        assert "Jev returned 500" in caplog.text

    def test_a_rejected_key_is_a_configuration_error(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, status_code=401, json={"detail": "Invalid API key"})

        with pytest.raises(ProviderConfigurationError, match="rejected the credentials"):
            _run(_node(jev_ids, session), session)

    def test_saves_the_turn_to_the_nodes_history(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())
        node = _node(jev_ids, session, decisions=[REFUND], history_type="node")

        _run(node, session)

        history = node.repo.get_pipeline_chat_history(node.history_type, node.node_id)
        messages = [(m.human_message, json.loads(m.ai_message)) for m in history.messages.all()]
        assert messages == [("I was charged twice", {"wants_refund": 0.98})]


class TestUsage:
    def test_one_call_is_recorded_against_the_jev_model(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answers())
        collector = MetricsCollector(start_time=time.time())

        _run(_node(jev_ids, session), session, config={"callbacks": [collector]})

        events = {
            (e.provider_type, e.model_name, e.service_kind, e.confidence): e.quantity
            for e in collector.iter_cost_events()
        }
        assert events == {
            ("jev", "jev-latest", ServiceKind.LLM_INPUT, Confidence.EXACT): 300,
            ("jev", "jev-latest", ServiceKind.LLM_OUTPUT, Confidence.EXACT): 30,
        }
        assert collector.get_metrics().n_turns == 1


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        pytest.param(("support", 0.9), "Support: I was charged twice", id="routes-on-the-choice"),
        pytest.param(("support", 0.3), "Billing: I was charged twice", id="below-threshold-takes-the-default"),
    ],
)
def test_a_static_router_routes_on_the_decision(httpx_mock, jev_ids, session, intent, expected):
    httpx_mock.add_response(url=URL, json=_answers(intent=intent))
    start = start_node()
    decide = decision_node(jev_ids, [INTENT])
    router = state_key_router_node("intent", ["billing", "support"], data_source=StaticRouterNode.DataSource.temp_state)
    billing = render_template_node("Billing: {{ input }}")
    support = render_template_node("Support: {{ input }}")
    end = end_node()
    edges = [
        {"id": "start -> decide", "source": start["id"], "target": decide["id"]},
        {"id": "decide -> router", "source": decide["id"], "target": router["id"]},
        {"id": "router -> billing", "source": router["id"], "target": billing["id"], "sourceHandle": "output_0"},
        {"id": "router -> support", "source": router["id"], "target": support["id"], "sourceHandle": "output_1"},
        {"id": "billing -> end", "source": billing["id"], "target": end["id"]},
        {"id": "support -> end", "source": support["id"], "target": end["id"]},
    ]
    runnable = create_runnable(PipelineFactory.create(), [start, decide, router, billing, support, end], edges)

    output = runnable.invoke(
        PipelineState(messages=["I was charged twice"], experiment_session=session),
        config={"configurable": {"repo": ORMRepository(session=session)}},
    )

    assert output["messages"][-1] == expected

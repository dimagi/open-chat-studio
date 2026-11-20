"""The Decision Node: several Jev questions in one call, answers written to the temporary state (#4631)."""

import json
import time
from unittest import mock

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.chat.exceptions import ProviderConfigurationError
from apps.cost_tracking.models import Confidence, ServiceKind
from apps.pipelines.exceptions import NodeUserConfigRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.nodes import DecisionNode
from apps.pipelines.repository import ORMRepository, RepositoryLookupError
from apps.pipelines.tests.utils import decision_routing_runnable
from apps.service_providers.llm_service.decision_backend import (
    ChoiceAnswer,
    DecisionBackend,
    DecisionLimits,
    DecisionResponse,
    DecisionUsage,
    ScoreAnswer,
    YesNoAnswer,
)
from apps.service_providers.llm_service.default_models import MODELS_BY_KEY, Model
from apps.service_providers.llm_service.jev import JevBackend
from apps.service_providers.tracing.metrics import MetricsCollector
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
            pytest.param(
                [REFUND, {**INTENT, "key": "wants_refund_confidence"}],
                "where wants_refund writes its confidence",
                id="yes-no-confidence",
            ),
            pytest.param([{**INTENT, "options": []}], "needs at least one option", id="choice-without-options"),
            pytest.param(
                [{**INTENT, "options": [{"name": str(i)} for i in range(256)]}],
                "needs 1 to 255 options",
                id="too-many-options",
            ),
            pytest.param([{**INTENT, "options": [{"name": " "}]}], "needs a name", id="blank-option"),
            pytest.param(
                [{**INTENT, "options": [{"name": "Yes"}, {"name": "YES"}]}], "more than once", id="repeated-option"
            ),
            pytest.param([{**FRUSTRATION, "levels": []}], "needs at least one level", id="score-without-levels"),
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

        with pytest.raises(PydanticValidationError, match="The Decision Node needs a decision model"):
            _node(ids)

    def test_the_model_picker_offers_only_jev(self):
        schema = DecisionNode.model_json_schema()["properties"]["llm_provider_id"]

        assert schema["ui:modelUse"] == "decisions"


class TestLimits:
    """The limits a decision is checked against come from the backend behind the selected model."""

    @pytest.fixture()
    def small_limits(self):
        limits = DecisionLimits(max_options=2, min_levels=2, max_levels=3, max_questions=2)
        with mock.patch.object(JevBackend, "limits", limits):
            yield

    @pytest.mark.parametrize(
        ("decisions", "message"),
        [
            pytest.param(
                [{**INTENT, "options": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}],
                "needs 1 to 2 options",
                id="too-many-options",
            ),
            pytest.param([{**FRUSTRATION, "levels": ["Calm"]}], "needs 2 to 3 levels", id="too-few-levels"),
            pytest.param([{**FRUSTRATION, "levels": list("abcd")}], "needs 2 to 3 levels", id="too-many-levels"),
            pytest.param([INTENT, FRUSTRATION, REFUND], "at most 2 decisions in one call", id="too-many-decisions"),
        ],
    )
    def test_rejects_what_the_backend_does_not_take(self, jev_ids, small_limits, decisions, message):
        with pytest.raises(PydanticValidationError, match=message):
            _node(jev_ids, decisions=decisions)

    def test_accepts_what_the_backend_takes(self, jev_ids, small_limits):
        node = _node(jev_ids, decisions=[{**INTENT, "options": [{"name": "a"}, {"name": "b"}]}, FRUSTRATION])

        assert len(node.decisions) == 2

    def test_a_limit_error_is_reported_against_the_decisions(self, jev_ids, small_limits):
        with pytest.raises(PydanticValidationError) as excinfo:
            _node(jev_ids, decisions=[{**FRUSTRATION, "levels": ["Calm"]}])

        assert excinfo.value.errors()[0]["ctx"]["field"] == "decisions"

    def test_upper_limits_are_skipped_when_the_provider_cannot_be_loaded(self, jev_ids):
        """The run reports a provider it cannot load; validation should not fail on it first."""
        with mock.patch.object(ORMRepository, "get_llm_service", side_effect=RepositoryLookupError("gone")):
            node = _node(jev_ids, decisions=[{**FRUSTRATION, "levels": ["x"] * 11}])

        assert len(node.decisions[0].levels) == 11

    @pytest.mark.parametrize(
        ("decision", "message"),
        [
            pytest.param({**INTENT, "options": []}, "needs at least one option", id="choice-without-options"),
            pytest.param({**FRUSTRATION, "levels": []}, "needs at least one level", id="score-without-levels"),
        ],
    )
    def test_an_empty_decision_is_rejected_when_the_provider_cannot_be_loaded(self, jev_ids, decision, message):
        """No backend takes a choice with nothing to choose or a score with no rubric, so this needs no backend."""
        with mock.patch.object(ORMRepository, "get_llm_service", side_effect=RepositoryLookupError("gone")):
            with pytest.raises(PydanticValidationError, match=message):
                _node(jev_ids, decisions=[decision])


def test_accepts_a_decision_model_listed_under_a_chat_provider(db):
    """A provider whose type also offers chat models can offer a decision model, and the node takes it."""
    provider = LlmProviderFactory.create(type="openai")
    model = LlmProviderModelFactory.create(team=provider.team, type="openai", name="gpt-decide")
    decision_model = Model("gpt-decide", 1000, chat=False, decisions=True)

    with mock.patch.dict(MODELS_BY_KEY, {("openai", "gpt-decide"): decision_model}):
        node = _node({"llm_provider_id": provider.id, "llm_provider_model_id": model.id}, decisions=[REFUND])

    assert node.llm_provider_model_id == model.id


class _FakeBackend(DecisionBackend):
    provider_type = "fake-decisions"
    name = "Fake"
    limits = DecisionLimits(max_options=10, min_levels=1, max_levels=10)

    def ask(self, model, input, questions):
        answers = {
            "intent": ChoiceAnswer("support", 0.9, {}),
            "frustration": ScoreAnswer(2.0, 0.8, {}),
            "wants_refund": YesNoAnswer(0.1, confidence=0.7),
        }
        return DecisionResponse(model, answers, DecisionUsage(50, 0), raw_answers={})

    def request_text(self, input, questions):
        return "fake request"


class TestBackend:
    def test_answers_from_any_backend_are_written(self, jev_ids, session):
        with mock.patch.object(DecisionNode, "get_decision_backend", return_value=_FakeBackend()):
            temp_state = _run(_node(jev_ids, session), session)["temp_state"]

        assert {key: temp_state[key] for key in ("intent", "frustration", "wants_refund")} == {
            "intent": "support",
            "frustration": 2.0,
            "wants_refund": 0.1,
        }

    def test_usage_is_recorded_under_the_backends_provider_type(self, jev_ids, session):
        collector = MetricsCollector(start_time=time.time())

        with mock.patch.object(DecisionNode, "get_decision_backend", return_value=_FakeBackend()):
            _run(_node(jev_ids, session), session, config={"callbacks": [collector]})

        events = {(e.provider_type, e.model_name, e.service_kind): e.quantity for e in collector.iter_cost_events()}
        assert events == {("fake-decisions", "jev-latest", ServiceKind.LLM_INPUT): 50}

    def test_a_provider_without_a_decision_backend_is_a_configuration_error(self, jev_ids, session):
        node = _node(jev_ids, session)

        with mock.patch("apps.service_providers.llm_service.JevLlmService.get_decision_backend", return_value=None):
            with pytest.raises(NodeUserConfigRunError, match="cannot answer decision questions"):
                _run(node, session)


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
    runnable = decision_routing_runnable(jev_ids, [INTENT], "intent", ["billing", "support"])

    output = runnable.invoke(
        PipelineState(messages=["I was charged twice"], experiment_session=session),
        config={"configurable": {"repo": ORMRepository(session=session)}},
    )

    assert output["messages"][-1] == expected

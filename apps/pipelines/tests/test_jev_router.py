"""The LLM Router routed by Jev instead of an LLM (#4575)."""

import json
import time
from unittest import mock

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.chat.exceptions import ProviderConfigurationError
from apps.cost_tracking.models import Confidence, ServiceKind
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import NodeContext
from apps.pipelines.nodes.jev_router import HISTORY_LIMIT
from apps.pipelines.nodes.nodes import (
    ExtractParticipantData,
    ExtractStructuredData,
    LLMResponse,
    LLMResponseWithPrompt,
    RouterNode,
)
from apps.pipelines.repository import ORMRepository
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory

URL = "https://api.typesafe.ai/v1/systemone"


def _answer(choice="billing", confidence=0.9):
    return {
        "model": "jev-1.14.0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": {"BILLING": confidence, "SUPPORT": 1 - confidence},
            }
        },
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


@pytest.fixture()
def jev_ids(db):
    provider = LlmProviderFactory.create(type="jev", config={"jev_api_key": "test-key"})
    model = LlmProviderModelFactory.create(team=provider.team, type="jev", name="jev-latest")
    return {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}


@pytest.fixture()
def session(db):
    return ExperimentSessionFactory.create()


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.jev.time.sleep"):
        yield


def _router(jev_ids, session, **params) -> RouterNode:
    router = RouterNode(
        node_id="router",
        django_node=None,
        name="router",
        keywords=["billing", "support"],
        route_descriptions={"billing": "Payments and invoices"},
        **jev_ids,
        **params,
    )
    router._repo = ORMRepository(session=session)
    return router


def _route(router: RouterNode, session, participant_data=None, config=None):
    router._config = config or {}
    state = PipelineState(
        messages=["I was charged twice"],
        experiment_session=session,
        last_node_input="I was charged twice",
        temp_state={"user_input": "I was charged twice"},
        participant_data=participant_data or {},
        session_state={"plan": "pro"},
    )
    return router._process_conditional(NodeContext(state))


def _sent(httpx_mock) -> dict:
    return json.loads(httpx_mock.get_request().content)


class TestRequest:
    def test_asks_one_choice_question_over_the_keywords(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())

        _route(_router(jev_ids, session), session)

        body = _sent(httpx_mock)
        assert body["model"] == "jev-latest"
        question = body["questions"]["route"]
        assert question["type"] == "choice"
        assert question["criteria"] == {"BILLING": "Payments and invoices", "SUPPORT": None}

    def test_instructions_are_the_routers_system_prompt(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())

        _route(_router(jev_ids, session, prompt_mode="custom", prompt="Pick for {session_state.plan}."), session)

        instructions = _sent(httpx_mock)["questions"]["route"]["instructions"]
        assert instructions == "Pick for pro.\nThe default routing destination is: BILLING"

    def test_state_carries_the_conversation(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())
        router = _router(jev_ids, session)
        router.save_history("Hi there", "SUPPORT")

        _route(router, session, participant_data={"name": "Ada", "score": float("nan")})

        state = _sent(httpx_mock)["state"]
        assert state["input"] == "I was charged twice"
        assert state["history"] == [
            {"role": "human", "content": "Hi there"},
            {"role": "ai", "content": "SUPPORT"},
        ]
        assert state["participant_data"] == {"name": "Ada", "score": None}
        assert state["session_state"] == {"plan": "pro"}
        assert state["temp_state"] == {"user_input": "I was charged twice"}

    def test_history_is_bounded_to_the_most_recent_messages(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())
        router = _router(jev_ids, session)
        for turn in range(HISTORY_LIMIT):
            router.save_history(f"question {turn}", "SUPPORT")

        _route(router, session)

        history = _sent(httpx_mock)["state"]["history"]
        assert len(history) == HISTORY_LIMIT
        assert history[-2]["content"] == f"question {HISTORY_LIMIT - 1}"

    def test_no_history_is_sent_when_history_is_off(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())
        router = _router(jev_ids, session, history_type="none")

        _route(router, session)

        assert _sent(httpx_mock)["state"]["history"] == []


class TestRouting:
    @pytest.mark.parametrize(
        ("answer", "threshold", "expected"),
        [
            pytest.param(_answer("billing", 0.9), 0.0, ("BILLING", False), id="takes-the-choice"),
            pytest.param(_answer("support", 0.05), 0.0, ("SUPPORT", False), id="no-threshold-accepts-anything"),
            pytest.param(_answer("support", 0.6), 0.6, ("SUPPORT", False), id="at-the-threshold"),
            pytest.param(_answer("support", 0.59), 0.6, ("BILLING", True), id="below-the-threshold"),
            pytest.param(_answer("refunds", 0.9), 0.0, ("BILLING", True), id="not-a-keyword"),
            pytest.param({**_answer(), "answers": {}}, 0.0, ("BILLING", True), id="no-answer"),
        ],
    )
    def test_route(self, httpx_mock, jev_ids, session, answer, threshold, expected):
        httpx_mock.add_response(url=URL, json=answer)

        assert _route(_router(jev_ids, session, confidence_threshold=threshold), session) == expected

    def test_saves_the_turn_to_the_routers_history(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer("support"))
        router = _router(jev_ids, session)

        _route(router, session)

        history = router.repo.get_pipeline_chat_history(router.history_type, router.node_id)
        assert [(m.human_message, m.ai_message) for m in history.messages.all()] == [("I was charged twice", "SUPPORT")]

    def test_an_unavailable_service_takes_the_default_route(self, httpx_mock, jev_ids, session, caplog):
        httpx_mock.add_response(url=URL, status_code=529, json={"detail": "Overloaded"})
        httpx_mock.add_response(url=URL, status_code=529, json={"detail": "Overloaded"})

        assert _route(_router(jev_ids, session), session) == ("BILLING", True)
        assert "Jev returned 529" in caplog.text

    def test_a_rejected_key_is_a_configuration_error(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, status_code=401, json={"detail": "Invalid API key"})

        with pytest.raises(ProviderConfigurationError, match="rejected the credentials"):
            _route(_router(jev_ids, session), session)

    def test_an_llm_provider_still_routes_with_the_agent(self, httpx_mock, session):
        router = RouterNode(
            node_id="router",
            django_node=None,
            name="router",
            keywords=["billing", "support"],
            llm_provider_id=LlmProviderFactory.create().id,
            llm_provider_model_id=LlmProviderModelFactory.create().id,
        )
        router._repo = ORMRepository(session=session)
        with mock.patch.object(RouterNode, "_route_with_agent", return_value="SUPPORT") as agent:
            assert _route(router, session) == ("SUPPORT", False)

        agent.assert_called_once()
        assert not httpx_mock.get_requests()


class TestUsage:
    def test_tokens_are_recorded_against_the_jev_model(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, json=_answer())
        collector = MetricsCollector(start_time=time.time())

        _route(_router(jev_ids, session), session, config={"callbacks": [collector]})

        events = {
            (e.provider_type, e.model_name, e.service_kind, e.confidence): e.quantity
            for e in collector.iter_cost_events()
        }
        assert events == {
            ("jev", "jev-latest", ServiceKind.LLM_INPUT, Confidence.EXACT): 120,
            ("jev", "jev-latest", ServiceKind.LLM_OUTPUT, Confidence.EXACT): 12,
        }
        assert collector.get_metrics().n_turns == 1

    def test_a_failed_call_records_nothing(self, httpx_mock, jev_ids, session):
        httpx_mock.add_response(url=URL, status_code=500, json={"detail": "boom"})
        collector = MetricsCollector(start_time=time.time())

        _route(_router(jev_ids, session), session, config={"callbacks": [collector]})

        assert list(collector.iter_cost_events()) == []


@pytest.mark.parametrize(
    ("node_class", "params"),
    [
        pytest.param(LLMResponse, {}, id="llm-response"),
        pytest.param(LLMResponseWithPrompt, {"prompt": "Be helpful"}, id="llm-response-with-prompt"),
        pytest.param(ExtractStructuredData, {"data_schema": '{"name": "a name"}'}, id="extract-structured-data"),
        pytest.param(ExtractParticipantData, {"data_schema": '{"name": "a name"}'}, id="extract-participant-data"),
    ],
)
def test_only_the_router_accepts_a_jev_provider(jev_ids, node_class, params):
    with pytest.raises(PydanticValidationError, match="Jev can only be used by the LLM Router node"):
        node_class(node_id="node", django_node=None, name="node", **jev_ids, **params)


def test_the_router_accepts_a_jev_provider(jev_ids, session):
    assert _router(jev_ids, session).llm_provider_id == jev_ids["llm_provider_id"]

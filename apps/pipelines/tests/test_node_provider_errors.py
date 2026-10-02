import httpx
import openai
import pytest
from pydantic import ConfigDict

from apps.chat.exceptions import ModelRefusedTurnError, ProviderConfigurationError
from apps.pipelines.nodes.base import NodeSchema, PipelineNode, PipelineRouterNode, PipelineState


class RaisingNode(PipelineNode):
    """A node whose only job is to raise whatever the test hands it."""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Raising"))

    def _process(self, state: PipelineState, context) -> PipelineState:
        raise self._error


class RaisingRouterNode(PipelineRouterNode):
    """The router path has its own translation block; this drives it."""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Raising router"))

    def get_output_map(self):
        return {"output_0": "true"}

    def _process_conditional(self, context):
        raise self._error


def _node(error: Exception) -> RaisingNode:
    # model_construct skips the django_node field, which this test has no use for.
    node = RaisingNode.model_construct(name="raiser", node_id="node-1")
    node._error = error
    return node


def _state() -> PipelineState:
    return PipelineState(messages=["hi"], outputs={}, experiment_session=None)


def _openai_error(cls, status, message, code):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls(message, response=httpx.Response(status, request=request), body={"message": message, "code": code})


def test_team_actionable_provider_error_is_translated_at_the_node_boundary():
    error = _openai_error(openai.RateLimitError, 429, "You have no credits remaining.", "credit_balance_exhausted")

    with pytest.raises(ProviderConfigurationError, match="no credit or quota remaining"):
        _node(error).process([], [], _state(), {})


def test_transient_provider_error_keeps_its_native_type_for_the_retry_policy():
    error = _openai_error(openai.RateLimitError, 429, "Rate limit reached", "rate_limit_exceeded")

    with pytest.raises(openai.RateLimitError):
        _node(error).process([], [], _state(), {})


def test_router_nodes_translate_too():
    error = _openai_error(openai.RateLimitError, 429, "You have no credits remaining.", "credit_balance_exhausted")
    node = RaisingRouterNode.model_construct(name="router", node_id="node-2")
    node._error = error
    router = node.build_router_function(edge_map={"output_0": "node-3"}, incoming_edges=[])

    with pytest.raises(ProviderConfigurationError, match="no credit or quota remaining"):
        router(_state(), {})


def test_content_filter_400_is_participant_actionable_at_the_node_boundary():
    error = _openai_error(openai.BadRequestError, 400, "The response was filtered", "content_filter")

    with pytest.raises(ModelRefusedTurnError) as exc_info:
        _node(error).process([], [], _state(), {})

    assert exc_info.value.kind == "content_filter"


def test_content_filter_400_in_a_router_ends_the_turn_with_the_participant_message():
    error = _openai_error(openai.BadRequestError, 400, "The response was filtered", "content_filter")
    node = RaisingRouterNode.model_construct(name="router", node_id="node-2")
    node._error = error
    router = node.build_router_function(edge_map={"output_0": "node-3"}, incoming_edges=[])

    with pytest.raises(ModelRefusedTurnError) as exc_info:
        router(_state(), {})

    assert exc_info.value.kind == "content_filter"
    assert str(exc_info.value) == "The last message was blocked by the provider's content filter."

import json
from unittest import mock

import pytest
from django.test import RequestFactory

from apps.help.agents.prompt_improve import PromptImproveAgent, PromptImproveInput, PromptImproveOutput
from apps.help.registry import AGENT_REGISTRY
from apps.help.views import run_agent

ORIGINAL = "You help people. Data: {participant_data}"
REWRITE = "You are a support assistant. Answer briefly.\n\nParticipant data: {participant_data}"


@pytest.fixture()
def llm():
    """The stubbed system agent; set `invoke.side_effect` to the outputs it should return in turn."""
    with mock.patch("apps.help.agents.prompt_improve.build_system_agent") as build:
        yield build.return_value


def _responses(*outputs: PromptImproveOutput) -> list[dict]:
    return [{"structured_response": output} for output in outputs]


def _user_messages(llm) -> list[str]:
    return [call.args[0]["messages"][0]["content"] for call in llm.invoke.call_args_list]


def _run(**kwargs) -> PromptImproveOutput:
    return PromptImproveAgent(input=PromptImproveInput(**{"prompt": ORIGINAL, **kwargs})).run()


def test_agent_is_registered():
    assert AGENT_REGISTRY["prompt_improve"] is PromptImproveAgent


def test_returns_a_valid_rewrite(llm):
    output = PromptImproveOutput(prompt=REWRITE, notes=["Stated the role."])
    llm.invoke.side_effect = _responses(output)

    assert _run() == output
    assert llm.invoke.call_count == 1


def test_user_message_carries_the_node_context(llm):
    llm.invoke.side_effect = _responses(PromptImproveOutput(prompt=REWRITE, notes=[]))

    _run(tool_names=["one-off-reminder"], instruction="Make it formal")

    [message] = _user_messages(llm)
    assert f"<prompt>\n{ORIGINAL}\n</prompt>" in message
    assert "<node_type>llm</node_type>" in message
    assert "one-off-reminder" in message
    assert "<instruction>\nMake it formal\n</instruction>" in message
    assert "source_material" in message


@pytest.mark.parametrize(
    ("rejected", "error"),
    [
        pytest.param("Be brief. {participant_data} {made_up}", "unknown variables: made_up", id="unknown-variable"),
        pytest.param("Be brief.", "must keep the variables: participant_data", id="dropped-variable"),
        pytest.param(
            "Be brief. {participant_data} {current_datetime}",
            "must not add the variables: current_datetime",
            id="added-variable",
        ),
        pytest.param(
            "{participant_data} and again {participant_data}",
            "participant_data is used more than once",
            id="duplicated-variable",
        ),
        pytest.param("Be brief. {participant_data", "Invalid format in prompt", id="unbalanced-brace"),
        pytest.param("Be brief. {participant_data!r}", "Invalid prompt variable", id="format-spec"),
        pytest.param("  ", "rewrite is empty", id="empty"),
    ],
)
def test_an_invalid_rewrite_is_retried_with_the_reason(llm, rejected, error):
    good = PromptImproveOutput(prompt=REWRITE, notes=["Stated the role."])
    llm.invoke.side_effect = _responses(PromptImproveOutput(prompt=rejected, notes=[]), good)

    assert _run() == good
    first, second = _user_messages(llm)
    assert "<validation_error>" not in first
    assert error in second


def test_returns_the_original_prompt_when_no_attempt_is_valid(llm):
    llm.invoke.return_value = {"structured_response": PromptImproveOutput(prompt="Be brief.", notes=["Shortened."])}

    result = _run()

    assert llm.invoke.call_count == PromptImproveAgent.max_attempts
    assert result.prompt == ORIGINAL
    assert result.notes == ["No valid rewrite was produced: The rewrite must keep the variables: participant_data"]


def test_router_prompts_allow_fewer_variables(llm):
    good = PromptImproveOutput(prompt="Route by topic. {participant_data}", notes=[])
    llm.invoke.side_effect = _responses(
        PromptImproveOutput(prompt="Route by topic. {participant_data} {source_material}", notes=[]), good
    )

    result = _run(prompt="Route. {participant_data}", node_type="router")

    assert result == good
    assert "unknown variables: source_material" in _user_messages(llm)[1]


def test_an_original_that_does_not_parse_is_not_compared(llm):
    output = PromptImproveOutput(prompt='Reply in JSON like {{"ok": true}}.', notes=["Escaped the braces."])
    llm.invoke.side_effect = _responses(output)

    assert _run(prompt='Reply in JSON like {"ok": true}.') == output


def test_nested_variables_count_as_their_root(llm):
    output = PromptImproveOutput(prompt="Greet {participant_data.name} by name.", notes=[])
    llm.invoke.side_effect = _responses(output)

    assert _run(prompt="Hi {participant_data.name}") == output


class TestPromptImproveView:
    def _post(self, body):
        request = RequestFactory().post("/help/prompt_improve/", data=json.dumps(body), content_type="application/json")
        request.team = mock.Mock(id=1)
        return run_agent.__wrapped__.__wrapped__(request, team_slug="test-team", agent_name="prompt_improve")

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"prompt": ""}, id="empty-prompt"),
            pytest.param({"prompt": "x", "node_type": "email"}, id="unknown-node-type"),
        ],
    )
    def test_invalid_input_returns_400(self, body):
        assert self._post(body).status_code == 400

    def test_returns_the_rewrite_and_notes(self, llm):
        llm.invoke.side_effect = _responses(PromptImproveOutput(prompt=REWRITE, notes=["Stated the role."]))

        response = self._post({"prompt": ORIGINAL, "tool_names": [], "instruction": ""})

        assert response.status_code == 200
        assert json.loads(response.content) == {"response": {"prompt": REWRITE, "notes": ["Stated the role."]}}

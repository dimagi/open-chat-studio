import json
from unittest import mock

import pytest
from django.urls import reverse

from apps.help.agents.prompt_improve import PromptImproveAgent, PromptImproveInput, PromptImproveOutput
from apps.help.registry import AGENT_REGISTRY
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

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


def test_router_message_lists_the_routes_and_marks_the_default(llm):
    llm.invoke.side_effect = _responses(PromptImproveOutput(prompt="Route. {participant_data}", notes=[]))

    _run(prompt="Route. {participant_data}", node_type="router", routes=["BILLING", "SUPPORT"], default_route="SUPPORT")

    [message] = _user_messages(llm)
    assert "<routes>\n- BILLING\n- SUPPORT (default)\n</routes>" in message


@pytest.mark.parametrize(
    ("node_type", "routes"),
    [
        pytest.param("llm", ["BILLING"], id="llm-node"),
        pytest.param("router", [], id="router-without-routes"),
    ],
)
def test_no_routes_section_without_router_routes(llm, node_type, routes):
    llm.invoke.side_effect = _responses(PromptImproveOutput(prompt="Route. {participant_data}", notes=[]))

    _run(prompt="Route. {participant_data}", node_type=node_type, routes=routes)

    assert "<routes>" not in _user_messages(llm)[0]


@pytest.mark.parametrize(
    ("node_type", "described", "not_described"),
    [
        pytest.param("llm", ["participant_data", "source_material", "temp_state"], [], id="llm"),
        pytest.param("router", ["participant_data", "temp_state"], ["source_material"], id="router"),
    ],
)
def test_user_message_describes_each_allowed_variable(llm, node_type, described, not_described):
    llm.invoke.side_effect = _responses(PromptImproveOutput(prompt="Route. {participant_data}", notes=[]))

    _run(prompt="Route. {participant_data}", node_type=node_type)

    [message] = _user_messages(llm)
    allowed = message.split("<allowed_variables>")[1].split("</allowed_variables>")[0]
    for name in described:
        assert f"- {name}: {PROMPT_VAR_DESCRIPTIONS[name]}" in allowed
    for name in not_described:
        assert name not in allowed


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


@pytest.mark.django_db()
class TestPromptImproveView:
    @pytest.fixture()
    def team(self, team_with_users):
        return team_with_users

    @pytest.fixture()
    def member_client(self, client, team):
        client.force_login(team.members.first())
        return client

    def _url(self, team):
        return reverse("help:run_agent", args=[team.slug, "prompt_improve"])

    def _post(self, client, team, body):
        return client.post(self._url(team), data=json.dumps(body), content_type="application/json")

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"prompt": ""}, id="empty-prompt"),
            pytest.param({"prompt": "x", "node_type": "email"}, id="unknown-node-type"),
        ],
    )
    def test_invalid_input_returns_400(self, member_client, team, body):
        assert self._post(member_client, team, body).status_code == 400

    def test_returns_the_rewrite_and_notes(self, member_client, team, llm):
        llm.invoke.side_effect = _responses(PromptImproveOutput(prompt=REWRITE, notes=["Stated the role."]))

        response = self._post(member_client, team, {"prompt": ORIGINAL, "tool_names": [], "instruction": ""})

        assert response.status_code == 200
        assert response.json() == {"response": {"prompt": REWRITE, "notes": ["Stated the role."]}}

    def test_rejects_a_get(self, member_client, team):
        assert member_client.get(self._url(team)).status_code == 405

    @pytest.mark.parametrize(
        ("login", "status"),
        [
            pytest.param(False, 302, id="anonymous-is-sent-to-login"),
            pytest.param(True, 404, id="non-member-gets-not-found"),
        ],
    )
    def test_requires_a_team_member(self, client, team, llm, login, status):
        if login:
            client.force_login(TeamWithUsersFactory.create().members.first())

        response = self._post(client, team, {"prompt": ORIGINAL})

        assert response.status_code == status
        llm.invoke.assert_not_called()

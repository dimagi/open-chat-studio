from typing import Any
from unittest.mock import Mock, patch

import pytest
from langchain_core.tools import BaseTool, Tool
from openai.types.beta.threads import RequiredActionFunctionToolCall
from openai.types.beta.threads.required_action_function_tool_call import Function
from openai.types.beta.threads.run import RequiredAction, RequiredActionSubmitToolOutputs

from apps.chat.agent.openapi_tool import ToolArtifact
from apps.service_providers.llm_service.adapters import AssistantAdapter
from apps.service_providers.llm_service.runnables import AgentAssistantChat
from apps.service_providers.tests.test_assistant_runnable import _create_run, _create_thread_messages
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service


def _make_tool_for_testing(output: Any, **kwargs) -> BaseTool:
    return Tool(name="fake_tool", description="Tool for testing", func=Mock(return_value=output), **kwargs)


@pytest.fixture()
def fake_llm_service():
    return build_fake_llm_service(responses=["this is a test message"], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    local_assistant = OpenAiAssistantFactory(assistant_id="assistant_1", tools=["fake_tool"])
    session = ExperimentSessionFactory(experiment__assistant=local_assistant)
    session.experiment.assistant.get_llm_service = lambda *args, **kwargs: fake_llm_service
    session.get_participant_data = lambda *args, **kwargs: {"name": "Tester"}
    return session


@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.runs.runs.Runs.submit_tool_outputs")
@patch("openai.resources.beta.threads.messages.Messages.list")
@patch("openai.resources.beta.threads.runs.Runs.retrieve")
@patch("openai.resources.beta.Threads.create_and_run")
@pytest.mark.django_db()
def test_assistant_tool_response(
    create_and_run, retrieve_run, list_messages, submit_tool_outputs, get_output_with_annotations, session
):
    run = configure_mocks(session, create_and_run, retrieve_run, list_messages, get_output_with_annotations)
    submit_tool_outputs.return_value = run

    tool = _make_tool_for_testing("test tool output")
    runnable = get_runnable(session, tool)

    result = runnable.invoke("test")
    assert result.output == "ai response"
    assert tool.func.call_args == (("test arg",), {})
    assert submit_tool_outputs.call_args == (
        (),
        {
            "tool_outputs": [{"output": "test tool output", "tool_call_id": "call1"}],
            "run_id": "test",
            "thread_id": "test_thread_id",
        },
    )


@patch("apps.assistants.sync.create_files_remote")  # called when tool output is being processed
@patch("apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations")
@patch("openai.resources.beta.threads.messages.Messages.create")  # called when tool output is submitted
@patch("openai.resources.beta.threads.messages.Messages.list")  # called in runnable to get run messages
@patch("openai.resources.beta.threads.runs.Runs.retrieve")  # called when 'waiting for run'
@patch("openai.resources.beta.threads.runs.Runs.create")  # called when tool output is submitted
@patch("openai.resources.beta.Threads.create_and_run")  # called during initial invoke
@pytest.mark.django_db()
def test_assistant_tool_artifact_response(
    create_and_run,
    create_run,
    retrieve_run,
    list_messages,
    create_message,
    get_output_with_annotations,
    create_files_remote,
    session,
):
    run = configure_mocks(session, create_and_run, retrieve_run, list_messages, get_output_with_annotations)
    create_run.return_value = run

    # mock the tool so that it returns an artifact. This should trigger the file upload workflow
    artifact = ToolArtifact(content=b"test artifact", filename="test.txt", content_type="text/plain")
    tool = _make_tool_for_testing(("test response", artifact), response_format="content_and_artifact")
    runnable = get_runnable(session, tool)

    create_files_remote.return_value = ["file-123abc"]

    result = runnable.invoke("test")

    assert result.output == "ai response"
    assert tool.func.call_args == (("test arg",), {})
    assert create_message.call_args == (
        ("test_thread_id",),
        {
            "role": "user",
            "content": "I have uploaded the results as a file for you to use.",
            "attachments": [{"file_id": "file-123abc", "tools": []}],
            "metadata": None,
        },
    )


def configure_mocks(session, create_and_run, retrieve_run, list_messages, get_output_with_annotations):
    get_output_with_annotations.return_value = ("ai response", {})
    thread_id = "test_thread_id"
    assistant_id = session.experiment.assistant.assistant_id
    run = _create_run(
        assistant_id,
        thread_id,
        status="requires_action",
        required_action=RequiredAction(
            submit_tool_outputs=RequiredActionSubmitToolOutputs(
                tool_calls=[
                    RequiredActionFunctionToolCall(
                        id="call1",
                        function=Function(name="fake_tool", arguments='{"arg1": "test arg"}'),
                        type="function",
                    )
                ]
            ),
            type="submit_tool_outputs",
        ),
    )
    create_and_run.return_value = run
    # 1st call on initial invoke, 2nd after tool message
    retrieve_run.side_effect = [run, _create_run(assistant_id, thread_id)]
    list_messages.return_value = _create_thread_messages(
        assistant_id, run.id, thread_id, [{"assistant": "ai response"}]
    )
    return run


def get_runnable(session, tool):
    with patch("apps.service_providers.llm_service.adapters.get_assistant_tools") as get_tools:
        get_tools.return_value = [tool]
        assistant_adapter = AssistantAdapter.for_experiment(session.experiment, session)
        runnable = AgentAssistantChat(adapter=assistant_adapter)
    return runnable

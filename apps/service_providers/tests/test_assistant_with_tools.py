from contextlib import contextmanager
from typing import Any
from unittest import mock
from unittest.mock import Mock, patch

import pytest
from langchain_core.tools import BaseTool, Tool
from openai.types.beta.threads import RequiredActionFunctionToolCall
from openai.types.beta.threads.required_action_function_tool_call import Function
from openai.types.beta.threads.run import RequiredAction, RequiredActionSubmitToolOutputs

from apps.chat.agent.openapi_tool import ToolArtifact
from apps.service_providers.llm_service.adapters import AssistantAdapter
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.llm_service.runnables import AgentAssistantChat
from apps.service_providers.tests.test_assistant_runnable import _create_run, _create_thread_messages
from apps.service_providers.tracing import TracingService
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
    return session


@patch("openai.resources.beta.threads.runs.runs.Runs.submit_tool_outputs")
@pytest.mark.django_db()
def test_assistant_tool_response(submit_tool_outputs, session):
    with configure_common_mocks(session) as run:
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


@patch("openai.resources.files.Files.create")  # called when tool output is being processed
@patch("openai.resources.beta.threads.messages.Messages.create")  # called when tool output is submitted
@patch("openai.resources.beta.threads.runs.Runs.create")  # called when tool output is submitted
@patch("openai.resources.beta.threads.runs.Runs.cancel", Mock())  # called when tool output is submitted
@pytest.mark.django_db()
@pytest.mark.parametrize(
    "builtin_tools", [(["code_interpreter"]), (["file_search"]), (["code_interpreter", "file_search"])]
)
def test_assistant_tool_artifact_response(create_run, create_message, create_files, session, builtin_tools):
    session.experiment.assistant.builtin_tools = builtin_tools

    with configure_common_mocks(session) as run:
        create_run.return_value = run

        # mock the tool so that it returns an artifact. This should trigger the file upload workflow
        artifact = ToolArtifact(content=b"test artifact", name="test_artifact.txt", content_type="text/plain")
        tool = _make_tool_for_testing(("test response", artifact), response_format="content_and_artifact")
        runnable = get_runnable(session, tool)
        create_files.return_value = Mock(id="file-123abc")

        result = runnable.invoke("test")

    assert result.output == "ai response"
    assert tool.func.call_args == (("test arg",), {})

    file_type_info = ""
    if "code_interpreter" in builtin_tools:
        file_type_info = (
            "\n\nFile type information:\n\n| File Path | Mime Type |\n| /mnt/data/file-123abc | text/plain |\n"
        )

    assert create_message.call_args == (
        ("test_thread_id",),
        {
            "role": "user",
            "content": f"I have uploaded the results as a file for you to use.{file_type_info}",
            "attachments": [{"file_id": "file-123abc", "tools": [{"type": builtin_tools[0]}] if builtin_tools else []}],
            "metadata": None,
        },
    )
    # check that the run was created with the correct tools (excluding the artifact tool)
    assert create_run.call_args_list == [
        mock.call("test_thread_id", assistant_id="assistant_1", tools=[{"type": tool} for tool in builtin_tools])
    ]


@contextmanager
def configure_common_mocks(session):
    with (
        patch(
            "apps.service_providers.llm_service.runnables.AssistantChat._get_output_with_annotations"
        ) as get_output_with_annotations,
        patch("openai.resources.beta.threads.messages.Messages.list") as list_messages,
        patch("openai.resources.beta.threads.runs.Runs.retrieve") as retrieve_run,
        patch("openai.resources.beta.Threads.create_and_run") as create_and_run,
    ):
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
        retrieve_run.side_effect = [
            run,  # 1st call on initial invoke
            _create_run(assistant_id, thread_id),  # send call waiting for cancellation
            _create_run(assistant_id, thread_id),  # 3rd call after tool message
        ]
        list_messages.return_value.data = _create_thread_messages(
            assistant_id, run.id, thread_id, [{"assistant": "ai response"}]
        )
        yield run


def get_runnable(session, tool):
    with patch("apps.service_providers.llm_service.adapters.get_assistant_tools") as get_tools:
        get_tools.return_value = [tool]
        assistant_adapter = AssistantAdapter.for_experiment(session.experiment, session)
        history_manager = ExperimentHistoryManager.for_assistant(session, session.experiment, TracingService.empty())
        runnable = AgentAssistantChat(adapter=assistant_adapter, history_manager=history_manager)
    return runnable

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Literal

import openai
from django.db import transaction
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain_core.agents import AgentFinish
from langchain_core.load import Serializable
from langchain_core.messages import BaseMessage
from langchain_core.messages.tool import ToolMessage, tool_call
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableSerializable,
    ensure_config,
)
from langchain_core.runnables.config import merge_configs
from pydantic import ConfigDict

from apps.chat.agent.openapi_tool import ToolArtifact
from apps.experiments.models import Experiment, ExperimentSession
from apps.files.models import File
from apps.service_providers.llm_service.adapters import AssistantAdapter, ChatAdapter
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager, PipelineHistoryManager
from apps.service_providers.llm_service.main import OpenAIAssistantRunnable
from apps.utils.prompt import OcsPromptTemplate

if TYPE_CHECKING:
    from apps.channels.datamodels import Attachment

logger = logging.getLogger("ocs.runnables")


class GenerationError(Exception):
    pass


class GenerationCancelled(Exception):
    def __init__(self, output: "ChainOutput"):
        self.output = output


def create_experiment_runnable(
    experiment: Experiment, session: ExperimentSession, disable_tools: bool = False, trace_service: Any = None
):
    """Create an experiment runnable based on the experiment configuration."""

    if assistant := experiment.assistant:
        history_manager = ExperimentHistoryManager.for_assistant(session=session, experiment=experiment)
        assistant_adapter = AssistantAdapter.for_experiment(experiment, session)
        if assistant.tools_enabled and not disable_tools:
            runnable = AgentAssistantChat(adapter=assistant_adapter, history_manager=history_manager)
        else:
            runnable = AssistantChat(adapter=assistant_adapter, history_manager=history_manager)
        # This is a temporary hack until we return an object with metadata about the run
        runnable.experiment = experiment
        return runnable

    assert experiment.llm_provider, "Experiment must have an LLM provider"
    assert experiment.llm_provider_model.name, "Experiment must have an LLM model"
    assert (
        experiment.llm_provider.type == experiment.llm_provider_model.type
    ), "Experiment provider and provider model should be of the same type"

    history_manager = ExperimentHistoryManager.for_llm_chat(
        session=session,
        experiment=experiment,
        trace_service=trace_service,
    )

    chat_adapter = ChatAdapter.for_experiment(experiment=experiment, session=session)
    if experiment.tools_enabled and not disable_tools:
        runnable = AgentLLMChat(adapter=chat_adapter, history_manager=history_manager)
    else:
        runnable = SimpleLLMChat(adapter=chat_adapter, history_manager=history_manager)

    # This is a temporary hack until we return an object with metadata about the run
    runnable.experiment = experiment
    return runnable


class ChainOutput(Serializable):
    output: str
    """String text."""
    prompt_tokens: int
    """Number of tokens in the prompt."""
    completion_tokens: int
    """Number of tokens in the completion."""

    type: Literal["OcsChainOutput"] = "ChainOutput"

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """Return whether this class is serializable."""
        return True

    @classmethod
    def get_lc_namespace(cls) -> list[str]:
        """Get the namespace of the langchain object."""
        return ["ocs", "schema", "chain_output"]


class LLMChat(RunnableSerializable[str, ChainOutput]):
    adapter: ChatAdapter
    history_manager: ExperimentHistoryManager | PipelineHistoryManager
    experiment: Experiment | None = None
    history: list[BaseMessage] = []
    cancelled: bool = False
    last_cancel_check: float | None = None
    check_every_ms: int = 1000
    input_key: str = "input"
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    def invoke(self, input: str, config: RunnableConfig | None = None, *args, **kwargs) -> ChainOutput:
        ai_message = None
        callback = self.adapter.callback_handler
        config = ensure_config(config)
        merged_config = merge_configs(ensure_config(config), {"callbacks": [callback]})
        configurable = config.get("configurable", {})
        include_conversation_history = configurable.get("include_conversation_history", True)
        save_input_to_history = configurable.get("save_input_to_history", True)
        save_output_to_history = configurable.get("save_output_to_history", True)
        experiment_tag = configurable.get("experiment_tag")

        try:
            if include_conversation_history:
                self._populate_memory(input)
            ai_message = self._get_output_check_cancellation(input, merged_config)
            result = ChainOutput(
                output=ai_message, prompt_tokens=callback.prompt_tokens, completion_tokens=callback.completion_tokens
            )
            if self.cancelled:
                raise GenerationCancelled(result)
        finally:
            self.history_manager.add_messages_to_history(
                input=input,
                save_input_to_history=save_input_to_history,
                input_message_metadata={},
                output=ai_message,
                save_output_to_history=save_output_to_history,
                experiment_tag=experiment_tag,
                output_message_metadata={},
            )

        return result

    def _get_input(self, input: str):
        return {self.input_key: self.adapter.format_input(input)}

    def _get_output_check_cancellation(self, input, config):
        chain = self._build_chain().with_config(run_name="get_llm_response")

        output = ""
        context = self._get_input_chain_context()
        for token in chain.stream({**self._get_input(input), **context}, config):
            output += self._parse_output(token)
            if self._chat_is_cancelled():
                return output
        return output

    def _parse_output(self, output):
        return output

    def _chat_is_cancelled(self):
        if self.cancelled:
            return True

        if self.last_cancel_check and self.check_every_ms:
            if self.last_cancel_check + self.check_every_ms > time.time():
                return False

        self.last_cancel_check = time.time()

        self.cancelled = self.adapter.check_cancellation()
        return self.cancelled

    def _build_chain(self) -> Runnable[dict[str, Any], Any]:
        raise NotImplementedError

    def _get_input_chain_context(self, with_history=True) -> dict:
        prompt = self.prompt
        context = self.adapter.get_template_context(prompt.input_variables)
        if with_history:
            context.update({"history": self.history})

        return context

    @property
    def prompt(self):
        return OcsPromptTemplate.from_messages(
            [
                ("system", self.adapter.get_prompt()),
                ("placeholder", "{history}"),
                ("human", "{input}"),
            ]
        )

    def _populate_memory(self, input: str):
        input_messages = self.get_input_messages(input)
        self.history = self.history_manager.get_chat_history(input_messages)

    def get_input_messages(self, input: str) -> list[BaseMessage]:
        """Return a list of messages which represent the fully populated LLM input.
        This will be used during history compression.
        """
        context = self._get_input_chain_context(with_history=False)
        try:
            return self.prompt.format_messages(**{**self._get_input(input), **context})
        except KeyError as e:
            raise GenerationError(str(e)) from e


class SimpleLLMChat(LLMChat):
    def _build_chain(self) -> Runnable[dict[str, Any], Any]:
        return self.prompt | self.adapter.get_chat_model() | StrOutputParser()


class AgentLLMChat(LLMChat):
    def _parse_output(self, output):
        return output.get("output", "")

    def _build_chain(self) -> Runnable[dict[str, Any], dict]:
        tools = self.adapter.get_allowed_tools()
        agent = create_tool_calling_agent(llm=self.adapter.get_chat_model(), tools=tools, prompt=self.prompt)
        return AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=tools,
            max_execution_time=120,
        )

    @property
    def prompt(self):
        prompt = super().prompt
        return OcsPromptTemplate.from_messages(prompt.messages + [("placeholder", "{agent_scratchpad}")])


class AssistantChat(RunnableSerializable[dict, ChainOutput]):
    adapter: AssistantAdapter
    history_manager: ExperimentHistoryManager | PipelineHistoryManager
    experiment: Experiment | None = None
    input_key: str = "content"
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def invoke(
        self, input: str, config: RunnableConfig | None = None, attachments: list["Attachment"] | None = None
    ) -> ChainOutput:
        callback = self.adapter.callback_handler
        config = ensure_config(config)
        merged_config = merge_configs(config, {"callbacks": [callback]})
        save_input_to_history = config.get("configurable", {}).get("save_input_to_history", True)
        experiment_tag = config.get("configurable", {}).get("experiment_tag")
        human_message_resource_file_ids = self._upload_tool_resource_files(attachments)
        human_message_metadata = self.adapter.get_input_message_metadata(human_message_resource_file_ids)
        ai_message = None
        ai_message_metadata = {}

        try:
            message_attachments = []
            for resource_name, openai_file_ids in human_message_resource_file_ids.items():
                message_attachments.extend(
                    [{"file_id": file_id, "tools": [{"type": resource_name}]} for file_id in openai_file_ids]
                )

            input_dict = {
                "content": self.adapter.format_input(input),
                "attachments": message_attachments,
            } | self._extra_input_configs()

            current_thread_id = self._sync_messages_to_thread(self.adapter.thread_id)

            if current_thread_id:
                input_dict["thread_id"] = current_thread_id
            input_dict["instructions"] = self.adapter.get_assistant_instructions()
            thread_id, run_id = self._get_response_with_retries(merged_config, input_dict, current_thread_id)
            ai_message, annotation_file_ids = self._get_output_with_annotations(thread_id, run_id)
            ai_message_metadata = self.adapter.get_output_message_metadata(annotation_file_ids)

            if not current_thread_id:
                self.adapter.thread_id = thread_id

        finally:
            self.history_manager.add_messages_to_history(
                input=input,
                save_input_to_history=save_input_to_history,
                input_message_metadata=human_message_metadata,
                output=ai_message,
                save_output_to_history=True,
                experiment_tag=experiment_tag,
                output_message_metadata=ai_message_metadata,
            )
        return ChainOutput(output=ai_message, prompt_tokens=0, completion_tokens=0)

    def _sync_messages_to_thread(self, current_thread_id):
        """Sync any messages that need to be sent to the thread. Create a new thread if necessary
        and return the thread ID.

        This is necessary in multi-bot setups if some of the bots are assistants but not all.
        """

        def _sync_messages_to_existing_thread(thread_id, messages):
            for message in messages_to_sync:
                self.adapter.assistant_client.beta.threads.messages.create(current_thread_id, **message)

        if messages_to_sync := self.adapter.get_messages_to_sync_to_thread():
            if current_thread_id:
                _sync_messages_to_existing_thread(current_thread_id, messages_to_sync)
            else:
                # There is a 32 message limit when creating a new thread
                first, rest = messages_to_sync[:32], messages_to_sync[32:]
                thread = self.adapter.assistant_client.beta.threads.create(messages=first)
                current_thread_id = thread.id
                _sync_messages_to_existing_thread(current_thread_id, rest)
                self.adapter.update_thread_id(current_thread_id)
        return current_thread_id

    @transaction.atomic()
    def _get_output_with_annotations(self, thread_id, run_id) -> tuple[str, list[str]]:
        """
        This makes a call to OpenAI with the `run_id` and `thread_id` to get more information about the response
        message, specifically regarding annotations.
        - Those of type `file_citation` cannot be read, but since we already have those files, we should be fine
        by only storing the reference to the file i.e. its external_id
        - Those of type `file_path` are generated and can be downloaded. A file is created in OCS for each of these

        """
        from apps.assistants.sync import get_and_store_openai_file

        client = self.adapter.assistant_client

        # We only want to the last message so that don't show 'thinking' messages
        # Don't iterate to avoid loading more pages
        messages_list = client.beta.threads.messages.list(thread_id, run_id=run_id, order="desc", limit=1).data
        if not messages_list:
            return "", []

        chat = self.adapter.session.chat
        session_id = self.adapter.session.id
        team = self.adapter.session.team
        assistant_file_ids = self.adapter.get_assistant_file_ids()

        file_ids = set()
        image_file_attachments = []
        file_path_attachments = []
        output_message = ""

        message = messages_list[0]
        for message_content in message.content:
            if message_content.type == "image_file":
                if created_file := self._create_image_file_from_image_message(client, message_content.image_file):
                    image_file_attachments.append(created_file)
                    file_ids.add(created_file.external_id)

                    team_slug = team.slug
                    file_link = f"file:{team_slug}:{session_id}:{created_file.id}"
                    output_message += f"![{created_file.name}]({file_link})\n"

            elif message_content.type == "text":
                message_content_value = message_content.text.value

                annotations = message_content.text.annotations
                for idx, annotation in enumerate(annotations):
                    file_id = None
                    file_ref_text = annotation.text
                    if annotation.type == "file_citation":
                        file_citation = annotation.file_citation
                        file_id = file_citation.file_id
                        file_name, file_link = self._get_file_link_for_citation(
                            file_id=file_id,
                            assistant_file_ids=assistant_file_ids,
                            allow_assistant_file_downloads=self.adapter.allow_assistant_file_downloads,
                        )

                        # Original citation text example:【6:0†source】
                        if self.adapter.citations_enabled:
                            message_content_value = message_content_value.replace(file_ref_text, f"[^{idx}]")
                            if file_link:
                                message_content_value += f"\n[^{idx}]: [{file_name}]({file_link})"
                            else:
                                message_content_value += f"\n\\[^{idx}\\]: {file_name}"
                        else:
                            message_content_value = message_content_value.replace(file_ref_text, "")

                    elif annotation.type == "file_path":
                        file_path = annotation.file_path
                        file_id = file_path.file_id
                        created_file = get_and_store_openai_file(
                            client=client,
                            file_id=file_id,
                            team_id=team.id,
                        )
                        # Original citation text example: sandbox:/mnt/data/the_file.csv.
                        # This is the link part in what looks like
                        # [Download the CSV file](sandbox:/mnt/data/the_file.csv)
                        message_content_value = message_content_value.replace(
                            file_ref_text, f"file:{team.slug}:{session_id}:{created_file.id}"
                        )
                        file_path_attachments.append(created_file)
                    file_ids.add(file_id)

                output_message += message_content_value + "\n"
            else:
                # Ignore any other type for now
                pass

        # Attach the generated files to the chat object as an annotation
        if file_path_attachments:
            resource, _created = chat.attachments.get_or_create(tool_type="file_path")
            resource.files.add(*file_path_attachments)

        if image_file_attachments:
            resource, _created = chat.attachments.get_or_create(tool_type="image_file")
            resource.files.add(*image_file_attachments)

        # replace all instance of `[some filename.pdf](https://example.com/download/file-abc)` with
        # just the link text
        output_message = re.sub(r"\[(?!\d+\])([^]]+)\]\([^)]+example\.com[^)]+\)", r"*\1*", output_message)

        return output_message.strip(), list(file_ids)

    def _create_image_file_from_image_message(self, client, image_file_message) -> File | None:
        """
        Creates a File record from `image_file_message` by pulling the data from OpenAI. Typically, these files don't
        have extentions, so we'll need to guess it based on the content. We know it will be an image, but not which
        extension to use.
        """
        from apps.assistants.sync import get_and_store_openai_file

        try:
            file_id = image_file_message.file_id
            return get_and_store_openai_file(
                client=client,
                file_id=file_id,
                team_id=self.adapter.team.id,
            )
        except Exception as ex:
            logger.exception(ex)

    def _get_file_link_for_citation(
        self, file_id: str, assistant_file_ids: list[str], allow_assistant_file_downloads: bool
    ) -> tuple[str, str | None]:
        """Returns a file name and a link constructor for `file_id`. If `file_id` is a member of
        `forbidden_file_ids`, the link will be empty to prevent unauthorized access.
        """
        file_link = ""

        team = self.adapter.session.team
        link_prefix = "assistant_file" if file_id in assistant_file_ids else "file"
        owner_id = self.adapter.assistant.id if file_id in assistant_file_ids else self.adapter.session.id

        try:
            file = File.objects.get(external_id=file_id, team_id=team.id)
            file_link = f"{link_prefix}:{team.slug}:{owner_id}:{file.id}"
            file_name = file.name
        except File.MultipleObjectsReturned:
            logger.error("Multiple files with the same external ID", extra={"file_id": file_id, "team": team.slug})
            file = File.objects.filter(external_id=file_id, team_id=team.id).first()
            file_link = f"{link_prefix}:{team.slug}:{owner_id}:{file.id}"
            file_name = file.name
        except File.DoesNotExist:
            client = self.adapter.assistant_client
            try:
                openai_file = client.files.retrieve(file_id=file_id)
                file_name = openai_file.filename
            except Exception as e:
                logger.error(f"Failed to retrieve file {file_id} from OpenAI: {e}")
                file_name = "Unknown File"

        if not allow_assistant_file_downloads and file_id in assistant_file_ids:
            # Don't allow downloading assistant level files
            return file_name, None

        return file_name, file_link

    def _upload_tool_resource_files(self, attachments: list["Attachment"] | None = None) -> dict[str, list[str]]:
        """Uploads the files in `attachments` to OpenAI

        Params:
            attachments - List of mappings between the resource type and the local file.
            Example:
                [{'code_interpreter': <File instance 1>}, {'code_interpreter': <File instance 2>}]

        Returns a mapping of resource to OpenAI file ids. Example:
            {'code_interpreter': ["file_id1", "file_id2"]}
        """
        from apps.assistants.sync import create_files_remote

        resource_file_ids = {}
        if not attachments:
            return resource_file_ids

        client = self.adapter.assistant_client

        for resource_name in ["file_search", "code_interpreter"]:
            file_ids = [att.file_id for att in attachments if att.type == resource_name]
            if resource_files := File.objects.filter(id__in=file_ids):
                # Upload the files to OpenAI
                openai_file_ids = create_files_remote(client, resource_files)
                resource_file_ids[resource_name] = openai_file_ids
        return resource_file_ids

    def _get_response_with_retries(self, config, input_dict, thread_id) -> tuple[str, str]:
        assistant_runnable = self.adapter.get_openai_assistant()

        for i in range(3):
            error = None
            try:
                return self._get_response(assistant_runnable, input_dict, config)
            except openai.BadRequestError as e:
                error = e
                self._handle_api_error(thread_id, assistant_runnable, e)
            except ValueError as e:
                error = e
                if re.search(r"cancelling|cancelled", str(e)):
                    raise GenerationCancelled(ChainOutput(output="", prompt_tokens=0, completion_tokens=0))
        raise GenerationError("Failed to get response after 3 retries") from error

    def _handle_api_error(self, thread_id: str, assistant_runnable: OpenAIAssistantRunnable, exc):
        """Handle OpenAI API errors.
        This should either raise an exception or return if the error was handled and the run should be retried.
        """
        message = exc.body.get("message") or ""
        match = re.match(r".*(thread_[\w]+) while a run (run_[\w]+) is active.*", message)
        if not match:
            raise exc

        error_thread_id, run_id = match.groups()
        if thread_id and error_thread_id != thread_id:
            raise GenerationError(f"Thread ID mismatch: {error_thread_id} != {thread_id}", exc)

        self._cancel_run(assistant_runnable, thread_id or error_thread_id, run_id)

    def _cancel_run(self, assistant_runnable, thread_id, run_id):
        logger.info("Cancelling run %s in thread %s", run_id, thread_id)
        assistant_runnable.client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
        assistant_runnable._wait_for_run(run_id, thread_id, progress_states=("in_progress", "queued", "cancelling"))

    def _get_response(self, assistant_runnable: OpenAIAssistantRunnable, input: dict, config: dict) -> tuple[str, str]:
        if self.adapter.tools:
            input["tools"] = []  # all tools are disabled
        response: OpenAIAssistantFinish = assistant_runnable.invoke(input, config)
        return response.thread_id, response.run_id

    def _extra_input_configs(self) -> dict:
        # Allow builtin tools but not custom tools when not running as an agent
        # This is to prevent using tools when using the assistant to generate responses
        # for automated messages e.g. reminders
        if self.adapter.assistant_tools_enabled and self.adapter.assistant_builtin_tools:
            return {"tools": [{"type": tool} for tool in self.adapter.assistant_builtin_tools]}

        # prefer not to specify if we don't need to
        return {}


class AgentAssistantChat(AssistantChat):
    def _extra_input_configs(self) -> dict:
        return {}

    def _get_response(self, assistant_runnable: OpenAIAssistantRunnable, input: dict, config: dict) -> tuple[str, str]:
        if self.adapter.disabled_tools:
            input["tools"] = self._get_allowed_tools(self.adapter.disabled_tools)

        response = assistant_runnable.invoke(input, config)
        max_time_limit = 60
        start_time = time.time()
        time_elapsed = 0.0
        max_iterations = 5
        iteration_count = 0
        while not isinstance(response, AgentFinish):
            if iteration_count >= max_iterations:
                logger.warning("Agent did not finish after %d iterations", max_iterations)
                response = response[0]
                break
            elif time_elapsed > max_time_limit:
                logger.warning("Agent did not finish after %d seconds", max_time_limit)
                response = response[0]
                break

            tool_outputs, tool_outputs_with_artifacts = self._invoke_tools(response)
            last_action = response[-1]

            has_tools = self.adapter.assistant_builtin_tools  # attachments need to be added to a tool
            if not has_tools or tool_outputs:
                # we can't mix normal outputs with artifacts
                for output in tool_outputs_with_artifacts:
                    tool_outputs.append({"output": output.content, "tool_call_id": output.tool_call_id})

                response = assistant_runnable.invoke(
                    {"tool_outputs": tool_outputs, "run_id": last_action.run_id, "thread_id": last_action.thread_id},
                    config,
                )
            else:
                response = self._handle_tool_artifacts(
                    tool_outputs_with_artifacts, assistant_runnable, last_action, config
                )

            time_elapsed = time.time() - start_time
            iteration_count += 1

        return response.thread_id, response.run_id

    def _invoke_tools(self, response) -> tuple[list, list]:
        tool_map = {tool.name: tool for tool in self.adapter.get_allowed_tools()}

        tool_outputs = []
        tool_outputs_with_artifacts = []

        for action in response:
            logger.info("Invoking tool %s", action.tool)
            tool = tool_map[action.tool]
            tool_output = tool.invoke(tool_call(name=action.tool, args=action.tool_input, id=action.tool_call_id))
            if isinstance(tool_output, ToolMessage):
                if tool_output.artifact:
                    tool_outputs_with_artifacts.append(tool_output)
                else:
                    tool_outputs.append({"output": tool_output.content, "tool_call_id": action.tool_call_id})
            else:
                tool_outputs.append({"output": tool_output, "tool_call_id": action.tool_call_id})

        return tool_outputs, tool_outputs_with_artifacts

    def _handle_tool_artifacts(self, tool_outputs_with_artifacts, assistant_runnable, last_action, config):
        """When artifacts are produced we don't submit the tool outputs to the existing run since
        that only accepts text.

        Instead, we create a new run with a new message and add the artifacts as attachments.
        """
        from apps.assistants.sync import _openai_create_file_with_retries

        logger.info(
            "Cancelling run %s. Starting new run for thread %s with attachments",
            last_action.run_id,
            last_action.thread_id,
        )
        assistant_runnable.client.beta.threads.runs.cancel(thread_id=last_action.thread_id, run_id=last_action.run_id)

        files = []
        seen_tools = set()
        for output in tool_outputs_with_artifacts:
            seen_tools.add(output.name)
            artifact = output.artifact
            if not isinstance(artifact, ToolArtifact):
                logger.warning("Unexpected artifact type %s", type(artifact))
                continue

            openai_file = _openai_create_file_with_retries(
                self.adapter.assistant_client, artifact.name, artifact.get_content()
            )
            files.append((openai_file.id, artifact.content_type))

        tools = []
        file_info_text = ""
        if "code_interpreter" in self.adapter.assistant_builtin_tools:
            tools = [{"type": "code_interpreter"}]
            file_infos = [{file_id: content_type} for file_id, content_type in files]
            file_info_text = self.adapter.get_file_type_info_text(file_infos)
        elif "file_search" in self.adapter.assistant_builtin_tools:
            tools = [{"type": "file_search"}]

        assistant_runnable._wait_for_run(
            last_action.run_id, last_action.thread_id, progress_states=("in_progress", "queued", "cancelling")
        )

        # only allow tools that weren't used in the previous run
        allowed_tools = self._get_allowed_tools(seen_tools)

        return assistant_runnable.invoke(
            {
                "content": "I have uploaded the results as a file for you to use." + file_info_text,
                "attachments": [{"file_id": file_id, "tools": tools} for file_id, _ in files],
                "thread_id": last_action.thread_id,
                "tools": allowed_tools,
            },
            config,
        )

    def _get_allowed_tools(self, disabled_tools: set[str]):
        from apps.assistants.sync import convert_to_openai_tool

        allowed_tools = [{"type": tool} for tool in self.adapter.assistant_builtin_tools if tool not in disabled_tools]
        if unused_tools := [tool for tool in self.adapter.get_allowed_tools() if tool.name not in disabled_tools]:
            allowed_tools.extend([convert_to_openai_tool(tool) for tool in unused_tools])
        return allowed_tools

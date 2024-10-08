import logging
import re
import time
from time import sleep
from typing import TYPE_CHECKING, Any, Literal

import openai
from django.db import models, transaction
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain.memory import ConversationBufferMemory
from langchain_core.load import Serializable
from langchain_core.memory import BaseMemory
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableLambda,
    RunnableSerializable,
    ensure_config,
)
from langchain_core.runnables.config import merge_configs

from apps.assistants.models import ToolResources
from apps.chat.models import Chat, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.files.models import File
from apps.service_providers.llm_service.main import OpenAIAssistantRunnable
from apps.service_providers.llm_service.state import (
    AssistantExperimentState,
    ChatExperimentState,
    ChatRunnableState,
)

if TYPE_CHECKING:
    from apps.channels.datamodels import Attachment

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    pass


class GenerationCancelled(Exception):
    def __init__(self, output: "ChainOutput"):
        self.output = output


def create_experiment_runnable(experiment: Experiment, session: ExperimentSession, disable_tools: bool = False):
    """Create an experiment runnable based on the experiment configuration."""
    state_kwargs = {"experiment": experiment, "session": session}
    if assistant := experiment.assistant:
        state = AssistantExperimentState(**state_kwargs)
        if assistant.tools_enabled and not disable_tools:
            return AssistantAgentRunnable(state=state)
        return AssistantExperimentRunnable(state=state)

    assert experiment.llm, "Experiment must have an LLM model"
    assert experiment.llm_provider, "Experiment must have an LLM provider"
    state = ChatExperimentState(**state_kwargs)
    if experiment.tools_enabled and not disable_tools:
        return AgentExperimentRunnable(state=state)

    return SimpleExperimentRunnable(state=state)


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


class ExperimentRunnable(RunnableSerializable[str, ChainOutput]):
    state: ChatRunnableState
    memory: BaseMemory = ConversationBufferMemory(return_messages=True, output_key="output", input_key="input")
    cancelled: bool = False
    last_cancel_check: float | None = None
    check_every_ms: int = 1000
    input_key: str = "input"

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    def invoke(self, input: str, config: RunnableConfig | None = None, *args, **kwargs) -> ChainOutput:
        callback = self.state.callback_handler
        config = ensure_config(config)
        merged_config = merge_configs(ensure_config(config), {"callbacks": [callback]})
        configurable = config.get("configurable", {})
        include_conversation_history = configurable.get("include_conversation_history", True)
        save_input_to_history = configurable.get("save_input_to_history", True)
        save_output_to_history = configurable.get("save_output_to_history", True)

        if include_conversation_history:
            self._populate_memory(input)

        if save_input_to_history:
            self.state.save_message_to_history(input, ChatMessageType.HUMAN)

        output = self._get_output_check_cancellation(input, merged_config)
        result = ChainOutput(
            output=output, prompt_tokens=callback.prompt_tokens, completion_tokens=callback.completion_tokens
        )
        if self.cancelled:
            raise GenerationCancelled(result)

        if save_output_to_history:
            experiment_tag = configurable.get("experiment_tag")
            self.state.save_message_to_history(output, ChatMessageType.AI, experiment_tag)
        return result

    def _get_input(self, input: str):
        return {self.input_key: self.state.format_input(input)}

    def _get_output_check_cancellation(self, input, config):
        chain = self._build_chain().with_config(run_name="get_llm_response")

        output = ""
        for token in chain.stream(self._get_input(input), config):
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

        self.cancelled = self.state.check_cancellation()
        return self.cancelled

    def _build_chain(self) -> Runnable[dict[str, Any], Any]:
        raise NotImplementedError

    def _get_input_chain_context(self, with_history=True):
        prompt = self.prompt
        context = {}
        if with_history:
            context.update(self.memory.load_memory_variables({}))

        if "source_material" in prompt.input_variables:
            context["source_material"] = self.state.get_source_material()

        if "participant_data" in prompt.input_variables:
            context["participant_data"] = self.state.get_participant_data()

        if "current_datetime" in prompt.input_variables:
            context["current_datetime"] = self.state.get_current_datetime()

        return RunnableLambda(lambda inputs: context | inputs, name="add_prompt_context")

    @property
    def prompt(self):
        return ChatPromptTemplate.from_messages(
            [
                ("system", self.state.get_prompt()),
                MessagesPlaceholder("history", optional=True),
                ("human", "{input}"),
            ]
        )

    def _populate_memory(self, input: str):
        input_messages = self.get_input_messages(input)
        self.memory.chat_memory.messages = self.state.get_chat_history(input_messages)

    def get_input_messages(self, input: str) -> list[BaseMessage]:
        """Return a list of messages which represent the fully populated LLM input.
        This will be used during history compression.
        """
        raise NotImplementedError

    @property
    def experiment(self) -> Experiment:
        return self.state.experiment


class SimpleExperimentRunnable(ExperimentRunnable):
    def get_input_messages(self, input: str):
        chain = self._input_chain(with_history=False).with_config(run_name="compute_input_for_compression")
        return chain.invoke(self._get_input(input)).to_messages()

    def _build_chain(self):
        return self._input_chain() | self.state.get_chat_model() | StrOutputParser()

    def _input_chain(self, with_history=True) -> Runnable[dict, PromptValue]:
        context = self._get_input_chain_context(with_history=with_history)
        return context | self.prompt


class AgentExperimentRunnable(ExperimentRunnable):
    def _parse_output(self, output):
        return output.get("output", "")

    def _input_chain(self, with_history=True) -> Runnable[dict[str, Any], dict]:
        return self._get_input_chain_context(with_history=with_history)

    def _build_chain(self):
        tools = self.state.get_tools()
        agent = self._input_chain() | create_tool_calling_agent(
            llm=self.state.get_chat_model(), tools=tools, prompt=self.prompt
        )
        executor = AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=tools,
            memory=self.memory,
            max_execution_time=120,
        )
        return executor

    @property
    def prompt(self):
        prompt = super().prompt
        return ChatPromptTemplate.from_messages(
            prompt.messages + [MessagesPlaceholder("agent_scratchpad", optional=True)]
        )

    def get_input_messages(self, input: str):
        chain = self._input_chain(with_history=False) | self.prompt
        chain = chain.with_config(run_name="compute_input_for_compression")
        return chain.invoke(self._get_input(input)).to_messages()


class AssistantExperimentRunnable(RunnableSerializable[dict, ChainOutput]):
    state: AssistantExperimentState
    input_key = "content"

    class Config:
        arbitrary_types_allowed = True

    def invoke(
        self, input: str, config: RunnableConfig | None = None, attachments: list["Attachment"] | None = None
    ) -> ChainOutput:
        human_message_resource_file_ids = self._upload_tool_resource_files(attachments)
        message_attachments = []
        for resource_name, openai_file_ids in human_message_resource_file_ids.items():
            message_attachments.extend(
                [{"file_id": file_id, "tools": [{"type": resource_name}]} for file_id in openai_file_ids]
            )

        callback = self.state.callback_handler
        config = ensure_config(config)
        merged_config = merge_configs(config, {"callbacks": [callback]})

        input_dict = {
            "content": self.state.format_input(input),
            "attachments": message_attachments,
        } | self._extra_input_configs()

        current_thread_id = self._sync_messages_to_thread(self.state.get_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID))

        if config.get("configurable", {}).get("save_input_to_history", True):
            file_ids = set([file_id for file_ids in human_message_resource_file_ids.values() for file_id in file_ids])
            self.state.save_message_to_history(input, ChatMessageType.HUMAN, annotation_file_ids=list(file_ids))

        if current_thread_id:
            input_dict["thread_id"] = current_thread_id
        input_dict["instructions"] = self.state.get_assistant_instructions()
        thread_id, run_id = self._get_response_with_retries(merged_config, input_dict, current_thread_id)
        output, annotation_file_ids = self._get_output_with_annotations(thread_id, run_id)

        if not current_thread_id:
            self.state.set_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID, thread_id)

        experiment_tag = config.get("configurable", {}).get("experiment_tag")
        self.state.save_message_to_history(
            output, ChatMessageType.AI, annotation_file_ids=annotation_file_ids, experiment_tag=experiment_tag
        )
        return ChainOutput(output=output, prompt_tokens=0, completion_tokens=0)

    def _sync_messages_to_thread(self, current_thread_id):
        """Sync any messages that need to be sent to the thread. Create a new thread if necessary
        and return the thread ID.

        This is necessary in multi-bot setups if some of the bots are assistants but not all.
        """

        def _sync_messages_to_existing_thread(thread_id, messages):
            for message in messages_to_sync:
                self.state.raw_client.beta.threads.messages.create(current_thread_id, **message)

        if messages_to_sync := self.state.get_messages_to_sync_to_thread():
            if current_thread_id:
                _sync_messages_to_existing_thread(current_thread_id, messages_to_sync)
            else:
                # There is a 32 message limit when creating a new thread
                first, rest = messages_to_sync[:32], messages_to_sync[32:]
                thread = self.state.raw_client.beta.threads.create(messages=first)
                current_thread_id = thread.id
                _sync_messages_to_existing_thread(current_thread_id, rest)
                self.state.set_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID, current_thread_id)
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

        client = self.state.raw_client
        chat = self.state.session.chat
        session_id = self.state.session.id

        team = self.state.session.team
        assistant_file_ids = ToolResources.objects.filter(assistant=self.state.experiment.assistant).values_list(
            "files"
        )
        assistant_files_ids = File.objects.filter(
            team_id=team.id, id__in=models.Subquery(assistant_file_ids)
        ).values_list("external_id", flat=True)

        file_ids = set()
        image_file_attachments = []
        file_path_attachments = []
        output_message = ""
        for message in client.beta.threads.messages.list(thread_id, run_id=run_id):
            for message_content in message.content:
                if message_content.type == "image_file":
                    if created_file := self._create_image_file_from_image_message(client, message_content.image_file):
                        image_file_attachments.append(created_file)
                        file_ids.add(created_file.external_id)

                        team_slug = self.state.session.team.slug
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
                                file_id=file_id, forbidden_file_ids=assistant_files_ids
                            )

                            # Original citation text example:【6:0†source】
                            message_content_value = message_content_value.replace(file_ref_text, f" [{idx}]")
                            if file_link:
                                message_content_value += f"\n[{idx}]: {file_link}"
                            else:
                                message_content_value += f"\n\\[{idx}\\]: {file_name}"

                        elif annotation.type == "file_path":
                            file_path = annotation.file_path
                            file_id = file_path.file_id
                            created_file = get_and_store_openai_file(
                                client=client,
                                file_id=file_id,
                                team_id=self.state.experiment.team_id,
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
                team_id=self.state.experiment.team_id,
            )
        except Exception as ex:
            logger.exception(ex)

    def _get_file_link_for_citation(self, file_id: str, forbidden_file_ids: list[str]) -> tuple[str, str | None]:
        """Returns a file name and a link constructor for `file_id`. If `file_id` is a member of
        `forbidden_file_ids`, the link will be empty to prevent unauthorized access.
        """
        file_link = ""

        team = self.state.session.team
        session_id = self.state.session.id
        try:
            file = File.objects.get(external_id=file_id, team_id=team.id)
            file_link = f"file:{team.slug}:{session_id}:{file.id}"
            file_name = file.name
        except File.DoesNotExist:
            client = self.state.raw_client
            openai_file = client.files.retrieve(file_id=file_id)
            try:
                openai_file = client.files.retrieve(file_id=file_id)
                file_name = openai_file.filename
            except Exception as e:
                logger.error(f"Failed to retrieve file {file_id} from OpenAI: {e}")
                file_name = "Unknown File"

        if file_id in forbidden_file_ids:
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

        client = self.state.get_llm_service().get_raw_client()

        for resource_name in ["file_search", "code_interpreter"]:
            file_ids = [att.file_id for att in attachments if att.type == resource_name]
            if resource_files := File.objects.filter(id__in=file_ids):
                # Upload the files to OpenAI
                openai_file_ids = create_files_remote(client, resource_files)
                resource_file_ids[resource_name] = openai_file_ids
        return resource_file_ids

    def _get_response_with_retries(self, config, input_dict, thread_id) -> tuple[str, str]:
        assistant_runnable = self.state.get_openai_assistant()

        for i in range(3):
            try:
                return self._get_response(assistant_runnable, input_dict, config)
            except openai.BadRequestError as e:
                self._handle_api_error(thread_id, assistant_runnable, e)
            except ValueError as e:
                if re.search(r"cancelling|cancelled", str(e)):
                    raise GenerationCancelled(ChainOutput(output="", prompt_tokens=0, completion_tokens=0))
        raise GenerationError("Failed to get response after 3 retries")

    def _handle_api_error(self, thread_id: str, assistant_runnable: OpenAIAssistantRunnable, exc):
        """Handle OpenAI API errors.
        This should either raise an exception or return if the error was handled and the run should be retried.
        """
        message = exc.body.get("message") or ""
        match = re.match(r".*(thread_[\w]+) while a run (run_[\w]+) is active.*", message)
        if not match:
            raise exc

        error_thread_id, run_id = match.groups()
        if error_thread_id != thread_id:
            raise GenerationError(f"Thread ID mismatch: {error_thread_id} != {thread_id}", exc)

        self._cancel_run(assistant_runnable, thread_id, run_id)

    def _cancel_run(self, assistant_runnable, thread_id, run_id):
        logger.info("Cancelling run %s in thread %s", run_id, thread_id)
        run = assistant_runnable.client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
        cancelling = run.status == "cancelling"
        while cancelling:
            run = assistant_runnable.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
            cancelling = run.status == "cancelling"
            if cancelling:
                sleep(assistant_runnable.check_every_ms / 1000)

    @property
    def experiment(self) -> Experiment:
        return self.state.experiment

    def _get_response(self, assistant_runnable: OpenAIAssistantRunnable, input: dict, config: dict) -> tuple[str, str]:
        response: OpenAIAssistantFinish = assistant_runnable.invoke(input, config)
        return response.thread_id, response.run_id

    def _extra_input_configs(self) -> dict:
        # Allow builtin tools but not custom tools when not running as an agent
        # This is to prevent using tools when using the assistant to generate responses
        # for automated messages e.g. reminders
        custom_tools = self.state.experiment.assistant.tools
        builtin_tools = self.state.experiment.assistant.builtin_tools
        if custom_tools and builtin_tools:
            return {"tools": [{"type": tool} for tool in builtin_tools]}

        # prefer not to specify if we don't need to
        return {}


class AssistantAgentRunnable(AssistantExperimentRunnable):
    def _extra_input_configs(self) -> dict:
        return {}

    def _get_response(self, assistant_runnable: OpenAIAssistantRunnable, input: dict, config: dict) -> tuple[str, str]:
        agent = AgentExecutor.from_agent_and_tools(
            agent=assistant_runnable,
            tools=self.state.get_tools(),
            max_execution_time=120,
        )
        response: dict = agent.invoke(input, config)
        return response["thread_id"], response["run_id"]

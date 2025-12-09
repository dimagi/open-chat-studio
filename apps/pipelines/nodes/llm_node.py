from __future__ import annotations

import logging
import operator
from string import Formatter
from typing import TYPE_CHECKING, Annotated

from langchain.agents import create_agent
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool
from langgraph.graph.message import (
    REMOVE_ALL_MESSAGES,
)

from apps.chat.agent.tools import SearchIndexTool, SearchToolConfig, get_node_tools
from apps.chat.conversation import COMPRESSION_MARKER
from apps.chat.models import ChatMessage
from apps.documents.models import Collection
from apps.experiments.models import ExperimentSession
from apps.files.models import File
from apps.pipelines.exceptions import PipelineNodeRunError
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatMessages
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.service_providers.llm_service.utils import (
    format_multimodal_input,
    populate_reference_section_from_citations,
    remove_citations_from_text,
)

if TYPE_CHECKING:
    from apps.pipelines.nodes.nodes import PipelineNode

logger = logging.getLogger("ocs.bots.summarization")


class StateSchema(AgentState):
    # allows tools to manipulate participant data and session state
    participant_data: Annotated[dict, operator.or_]
    session_state: Annotated[dict, operator.or_]


def execute_sub_agent(node, state: PipelineState):
    user_input = state["last_node_input"]
    session: ExperimentSession | None = state.get("experiment_session")
    tool_callbacks = ToolCallbacks()
    agent = build_node_agent(node, state, session, tool_callbacks)

    attachments = [att for att in state.get("temp_state", {}).get("attachments", [])]
    formatted_input = format_multimodal_input(message=user_input, attachments=attachments)

    inputs = StateSchema(
        messages=[formatted_input],
        participant_data=state.get("participant_data") or {},
        session_state=state.get("session_state") or {},
    )
    result = agent.invoke(inputs)
    final_message = result["messages"][-1]

    ai_message, ai_message_metadata = _process_agent_output(node, session, final_message)

    node.save_history(session, user_input, ai_message)

    voice_kwargs = {}
    if node.synthetic_voice_id is not None:
        voice_kwargs["synthetic_voice_id"] = node.synthetic_voice_id

    return PipelineState.from_node_output(
        node_name=node.name,
        node_id=node.node_id,
        output=ai_message,
        output_message_metadata={
            **ai_message_metadata,
            **tool_callbacks.output_message_metadata,
        },
        intents=tool_callbacks.intents,
        participant_data=result.get("participant_data") or {},
        session_state=result.get("session_state") or {},
        **voice_kwargs,
    )


def _process_agent_output(node, session, message):
    output_parser = node.get_llm_service().get_output_parser()
    parsed_output = output_parser(message.content, session=session, include_citations=node.generate_citations)
    ai_message_metadata = _process_files(
        session, cited_files=parsed_output.cited_files, generated_files=parsed_output.generated_files
    )
    if node.generate_citations:
        ai_message = populate_reference_section_from_citations(
            parsed_output.text, cited_files=parsed_output.cited_files, session=session
        )
    else:
        ai_message = remove_citations_from_text(parsed_output.text)

    return ai_message, ai_message_metadata


class HistoryCompressionMiddleware(SummarizationMiddleware):
    """
    Middleware to summarize chat history based on node configuration.

    History is loaded into the state in the before_agent step, and summaries are persisted
    to the database in the before_model step, if a summary is generated.
    """

    def __init__(self, session, node: PipelineNode, **kwargs):
        super().__init__(model=node.get_chat_model(), **kwargs)
        self.session = session
        self.node = node

    def before_agent(self, state, runtime):
        return {
            "messages": [
                # Since this response will get merged with the existing state messages, we cannot simply append the
                # history to the user's message. We need to replace the full message history in the state.
                # See https://github.com/langchain-ai/langchain/blob/c63f23d2339b2604edc9ae1d9f7faf7d6cc7dc78/libs/langchain_v1/langchain/agents/middleware/summarization.py#L286-L292
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self.node.get_history(self.session),
                *state["messages"],
            ]
        }

    def before_model(self, state, runtime):
        result = super().before_model(state, runtime)
        if result is not None:
            self._persist_summary(result["messages"])

        return result

    def _create_summary(self, messages_to_summarize: list) -> str:
        """
        Create summary based on node's history mode
        TRUNCATE_TOKENS and MAX_HISTORY_LENGTH mode does not create summaries, it only removes old messages until under
        token or message limit.
        """
        if self.node.history_mode == PipelineChatHistoryModes.TRUNCATE_TOKENS:
            return COMPRESSION_MARKER
        elif self.node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
            # We don't bother creating a summary
            return ""
        return super()._create_summary(messages_to_summarize)

    def _build_new_messages(self, summary: str) -> list:
        if self.node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
            # Don't include a summary message
            return []

        return super()._build_new_messages(summary)

    def _persist_summary(self, messages: list[BaseMessage]):
        history_mode = self.node.get_history_mode()

        checkpoint_message_id = self._find_latest_message_db_id(messages)
        if not checkpoint_message_id:
            # This should not happen, so we log it as an exception to surface it if it does
            logger.exception(
                "Unable to persist summary", extra={"node_id": self.node.node_id, "history_mode": history_mode}
            )
            return

        # The first message is always a RemoveMessage if a summary was created
        summary_message = messages[1]
        summary = summary_message.content

        if self.node.use_session_history():
            if summary == COMPRESSION_MARKER:
                metadata = {"compression_marker": history_mode}
            else:
                metadata = {"summary": summary}
            message = ChatMessage.objects.get(id=checkpoint_message_id)
            message.metadata.update(metadata)
            message.save(update_fields=["metadata"])
        else:
            # Use pipeline history
            updates = {"compression_marker": history_mode}
            if summary != COMPRESSION_MARKER:
                updates["summary"] = summary
            PipelineChatMessages.objects.filter(id=checkpoint_message_id).update(**updates)

    def _find_latest_message_db_id(self, messages: list) -> str | None:
        """
        Find the database ID of the latest message in the list that has one.
        Typically it will be at index -2, since the one at -1 is the new user message.
        """

        for i in range(len(messages) - 2, -1, -1):
            if "id" in messages[i].additional_kwargs:
                return messages[i].additional_kwargs["id"]


def get_history_compression_middleware(node, session, system_message) -> HistoryCompressionMiddleware | None:
    """
    Return the history compression middleware configured for this node.

    If ``history_type`` is ``NONE`` no middleware is attached; all other modes require
    compression. The ``history_mode`` determines the ``trigger`` and ``keep`` thresholds
    used to summarize the conversation history.

    Accounting for the system message:
    Since the system message is also takes up tokens in the context window, we need to
    subtract its token count from the total token limit when calculating the thresholds.
    """
    from apps.pipelines.nodes.nodes import get_llm_provider_model

    # TODO: Use the token counter from the LLM service
    system_message_tokens = count_tokens_approximately([system_message])

    if node.history_is_disabled():
        return None

    if node.history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        trigger = ("messages", 2)  # make this a low number to always trigger
        keep = ("messages", node.max_history_length)
    else:
        specified_token_limit = (
            node.user_max_token_limit
            if node.user_max_token_limit is not None
            else get_llm_provider_model(node.llm_provider_model_id).max_token_limit
        )
        token_limit = max(specified_token_limit - system_message_tokens, 100)

        trigger = ("tokens", token_limit)
        if node.history_mode == PipelineChatHistoryModes.SUMMARIZE:
            keep = ("messages", 20)
        else:
            keep = ("tokens", token_limit)

    return HistoryCompressionMiddleware(
        session=session, node=node, trigger=trigger, keep=keep, system_message=system_message
    )


def build_node_agent(node, pipeline_state: PipelineState, session: ExperimentSession, tool_callbacks: ToolCallbacks):
    prompt_context = _get_prompt_context(node, session, pipeline_state)
    tools = _get_configured_tools(node, session=session, tool_callbacks=tool_callbacks)
    system_message = _get_system_message(node, prompt_context=prompt_context)

    middleware = []
    if history_middleware := get_history_compression_middleware(node, session=session, system_message=system_message):
        middleware.append(history_middleware)

    return create_agent(
        # TODO: I think this will fail with google builtin tools
        model=node.get_chat_model(),
        tools=tools,
        system_prompt=system_message,
        middleware=middleware,
        state_schema=StateSchema,
    )


def _get_system_message(node, prompt_context: PromptTemplateContext) -> SystemMessage:
    system_message_template = node.prompt
    input_variables = {v for _, v, _, _ in Formatter().parse(system_message_template) if v is not None}
    context = prompt_context.get_context(input_variables)
    try:
        system_message = system_message_template.format(**context)
        return SystemMessage(content=system_message)
    except KeyError as e:
        raise PipelineNodeRunError(str(e)) from e


def _process_files(session: ExperimentSession, cited_files: set[File], generated_files: set[File]) -> dict:
    """`cited_files` is a list of files that are cited in the response whereas generated files are those generated
    by the LLM
    """
    if cited_files:
        session.chat.attach_files(attachment_type="file_citation", files=cited_files)
    if generated_files:
        session.chat.attach_files(attachment_type="code_interpreter", files=generated_files)
    return {
        "cited_files": [file.id for file in cited_files],
        "generated_files": [file.id for file in generated_files],
    }


def _get_prompt_context(node, session: ExperimentSession, state: PipelineState):
    extra_prompt_context = {
        "temp_state": state.get("temp_state") or {},
        "session_state": state.get("session_state") or {},
    }
    return PromptTemplateContext(
        session,
        source_material_id=node.source_material_id,
        collection_id=node.collection_id,
        collection_index_ids=node.collection_index_ids,
        extra=extra_prompt_context,
        participant_data=state.get("participant_data") or {},
    )


def _get_configured_tools(node, session: ExperimentSession, tool_callbacks: ToolCallbacks) -> list[dict | BaseTool]:
    """Get instantiated tools for the given node configuration."""
    tools = get_node_tools(node.django_node, session, tool_callbacks=tool_callbacks)
    tools.extend(node.get_llm_service().attach_built_in_tools(node.built_in_tools, node.tool_config))
    if search_tool := _get_search_tool(node):
        tools.append(search_tool)

    if node.disabled_tools:
        # Model builtin tools doesn't have a name attribute and are dicts
        return [tool for tool in tools if hasattr(tool, "name") and tool.name not in node.disabled_tools]
    return tools


def _get_search_tool(node):
    from apps.chat.agent.tools import SearchCollectionByIdTool
    from apps.service_providers.llm_service.main import OpenAIBuiltinTool

    if not node.collection_index_ids:
        return None

    collections = list(Collection.objects.filter(id__in=node.collection_index_ids, is_index=True))
    if not collections:
        # collections probably deleted
        return None

    if len(collections) == 1:
        # Single collection: use the existing single-index search tool
        collection = collections[0]
        if collection.is_remote_index:
            return OpenAIBuiltinTool(
                type="file_search",
                vector_store_ids=[collection.openai_vector_store_id],
                max_num_results=node.max_results,
            )

        search_config = SearchToolConfig(
            index_id=collection.id, max_results=node.max_results, generate_citations=node.generate_citations
        )
        search_tool = SearchIndexTool(search_config=search_config)
        return search_tool

    # Multiple collections: check if they're remote or local
    first_collection = collections[0]

    if first_collection and first_collection.is_remote_index:
        # All remote: create OpenAI builtin tool with multiple vector stores
        # We can assume this is true because of the node validation

        vector_store_ids = [collection.openai_vector_store_id for collection in collections]
        return OpenAIBuiltinTool(
            type="file_search",
            vector_store_ids=vector_store_ids,
            max_num_results=node.max_results,
        )
    else:
        # All local: use the multi-index search tool
        search_tool = SearchCollectionByIdTool(
            max_results=node.max_results,
            generate_citations=node.generate_citations,
            allowed_collection_ids=node.collection_index_ids,
        )
        return search_tool

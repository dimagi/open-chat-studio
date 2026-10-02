from __future__ import annotations

import logging
import operator
from typing import TYPE_CHECKING, Annotated, cast

from langchain.agents import create_agent
from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage, HumanMessage

from apps.chat.agent.tools import SearchCollectionByIdTool, SearchIndexTool, SearchToolConfig, get_node_tools
from apps.chat.models import ChatMessageMetadataKeys
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.helpers import get_agent_middleware, get_system_message, prompt_uses_current_datetime
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.service_providers.llm_service.main import OpenAIBuiltinTool
from apps.service_providers.llm_service.outcomes import LENGTH_REASONS, classify_turn, raise_for_outcome
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.service_providers.llm_service.utils import (
    format_multimodal_input,
    invoke_with_image_error_translation,
    populate_reference_section_from_citations,
    remove_citations_from_text,
)

logger = logging.getLogger("ocs.pipelines.nodes")

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from apps.experiments.models import ExperimentSession
    from apps.files.models import File
    from apps.pipelines.nodes.context import NodeContext
    from apps.service_providers.llm_service.datamodels import LlmChatResponse


class StateSchema(AgentState):
    # allows tools to manipulate participant data and session state
    participant_data: Annotated[dict, operator.or_]
    session_state: Annotated[dict, operator.or_]
    input_message_id: Annotated[int | None, operator.or_]


def execute_sub_agent(node: PipelineNode, context: NodeContext):
    user_input = context.input
    session = context.session
    tool_callbacks = ToolCallbacks()
    prompt_context = _get_prompt_context(node, session, context)
    agent = build_node_agent(node, context, session, tool_callbacks, prompt_context=prompt_context)

    attachments = list(context.attachments)
    supported_image_types = node.get_llm_service().supported_image_content_types
    formatted_input = format_multimodal_input(
        message=user_input, attachments=attachments, supported_image_content_types=supported_image_types
    )
    _add_current_datetime_to_turn(node, prompt_context, formatted_input)

    inputs = StateSchema(
        messages=[formatted_input],
        participant_data=context.state.participant_data or {},
        session_state=context.state.session_state or {},
        input_message_id=context.input_message_id,
    )
    result = invoke_with_image_error_translation(agent, inputs, supported_image_content_types=supported_image_types)
    final_message = _get_final_ai_message(result["messages"])
    outcome = classify_turn(final_message)
    if outcome.kind != "answered" or outcome.provider_reason in LENGTH_REASONS:
        logger.info(
            "LLM node %s turn outcome %s (provider reason: %s)", node.node_id, outcome.kind, outcome.provider_reason
        )
    raise_for_outcome(outcome, node.name)

    ai_message, ai_message_metadata = _process_agent_output(node, session, final_message)

    node.save_history(user_input, ai_message)

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


def _process_agent_output(node: PipelineNode, session: ExperimentSession, message: AIMessage):
    output_parser = node.get_llm_service().get_output_parser()
    parsed_output: LlmChatResponse = output_parser(
        output=message, session=session, include_citations=node.generate_citations
    )
    ai_message_metadata = _process_files(
        node, cited_files=parsed_output.cited_files, generated_files=parsed_output.generated_files
    )
    if node.generate_citations:
        ai_message = populate_reference_section_from_citations(
            parsed_output.text, cited_files=parsed_output.cited_files, session=session
        )
    else:
        ai_message = remove_citations_from_text(parsed_output.text)

    return ai_message, ai_message_metadata


def build_node_agent(
    node: PipelineNode,
    context: NodeContext,
    session: ExperimentSession,
    tool_callbacks: ToolCallbacks,
    prompt_context: PromptTemplateContext,
):
    tools = _get_configured_tools(node, session=session, tool_callbacks=tool_callbacks)
    system_message = get_system_message(prompt_template=node.prompt, prompt_context=prompt_context)

    middleware = get_agent_middleware(node, system_message)

    return create_agent(
        # TODO: I think this will fail with google builtin tools
        model=node.get_chat_model(),
        tools=tools,
        system_prompt=system_message,
        middleware=middleware,
        state_schema=StateSchema,
    )


def _process_files(node: PipelineNode, cited_files: set[File], generated_files: set[File]) -> dict:
    """`cited_files` is a list of files that are cited in the response whereas generated files are those generated
    by the LLM
    """
    if cited_files:
        node.repo.attach_files_to_chat(attachment_type="file_citation", files=cited_files)
    if generated_files:
        node.repo.attach_files_to_chat(attachment_type="code_interpreter", files=generated_files)
    return {
        ChatMessageMetadataKeys.CITED_FILES: [file.id for file in cited_files],
        ChatMessageMetadataKeys.GENERATED_FILES: [file.id for file in generated_files],
    }


def _add_current_datetime_to_turn(
    node: PipelineNode, prompt_context: PromptTemplateContext, message: HumanMessage
) -> None:
    """Prepend the precise, tz-aware current datetime to the latest message turn.

    Keeping the volatile value out of the cached system prompt prefix (see ``get_system_message``)
    and on the newest, uncached turn instead lets prompt caching keep the large stable prefix warm
    across turns. Only injected when the node's prompt opts into ``{current_datetime}``. See #3625.
    """
    if not prompt_uses_current_datetime(node.prompt):
        return

    datetime_block = {
        "type": "text",
        "text": f"<current_datetime>{prompt_context.get_current_datetime()}</current_datetime>",
    }
    if isinstance(message.content, list):
        message.content.insert(0, datetime_block)
    else:
        message.content = [datetime_block, {"type": "text", "text": message.content}]


def _get_prompt_context(node: PipelineNode, session: ExperimentSession, context: NodeContext):
    extra_prompt_context = {
        "temp_state": context.state.temp or {},
        "session_state": context.state.session_state or {},
    }
    return PromptTemplateContext(
        session,
        source_material_id=node.source_material_id,
        collection_id=node.collection_id,
        collection_index_ids=node.collection_index_ids,
        extra=extra_prompt_context,
        participant_data=context.state.participant_data or {},
        repo=node.repo,
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
    return cast("list[dict | BaseTool]", tools)


def _get_search_tool(node):
    if not node.collection_index_ids:
        return None

    collections = node.repo.get_collections_for_search(node.collection_index_ids)
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
        return SearchIndexTool(search_config=search_config)

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
        return SearchCollectionByIdTool(
            max_results=node.max_results,
            generate_citations=node.generate_citations,
            allowed_collection_ids=node.collection_index_ids,
        )


def _get_final_ai_message(messages: list) -> AIMessage:
    """Return this turn's reply: the last AI message with text, or the refused or filtered turn that ended it.

    Claude and some other models answer alongside tool calls and then send further turns
    that carry only tool calls or an empty content array, so the walk-back returns the last
    message that has text. It stops at a refused or filtered turn so an earlier answer is not
    delivered in its place. Only this turn's messages are considered: replayed history
    carries the message's DB id in ``additional_kwargs``, the way ``ChatMessage.to_langchain_dict``
    writes it, and messages produced in this turn do not.
    """
    this_turn = [m for m in messages if "id" not in m.additional_kwargs]
    ai_messages = [m for m in this_turn if isinstance(m, AIMessage)]
    for message in reversed(ai_messages):
        if message.text or classify_turn(message).kind in ("refusal", "content_filter"):
            return message
    return ai_messages[-1] if ai_messages else AIMessage(content="")

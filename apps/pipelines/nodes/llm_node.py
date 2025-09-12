from langchain_core.messages import SystemMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import BaseTool
from langgraph.prebuilt.chat_agent_executor import AgentState, create_react_agent

from apps.chat.agent.tools import get_node_tools
from apps.documents.models import Collection
from apps.experiments.models import ExperimentSession
from apps.files.models import File
from apps.pipelines.exceptions import PipelineNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.tool_callbacks import ToolCallbacks
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.service_providers.llm_service.utils import (
    format_multimodal_input,
    populate_reference_section_from_citations,
    remove_citations_from_text,
)


def execute_sub_agent(node, state: PipelineState, user_input: str):
    session: ExperimentSession | None = state.get("experiment_session")
    tool_callbacks = ToolCallbacks()
    agent = build_node_agent(node, state, session, tool_callbacks)

    attachments = [att for att in state.get("temp_state", {}).get("attachments", [])]
    formatted_input = format_multimodal_input(message=user_input, attachments=attachments)

    result = agent.invoke({"messages": [formatted_input]})
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


def build_node_agent(node, state: PipelineState, session: ExperimentSession, tool_callbacks: ToolCallbacks):
    prompt_context = _get_prompt_context(node, session, state)

    tools = _get_configured_tools(node, session=session, tool_callbacks=tool_callbacks)

    def prompt_callable(state: AgentState):
        prompt_template = PromptTemplate.from_template(node.prompt)
        context = prompt_context.get_context(prompt_template.input_variables)
        try:
            formatted_prompt = prompt_template.format(**context)
            prompt = SystemMessage(content=formatted_prompt)
        except KeyError as e:
            raise PipelineNodeRunError(str(e)) from e

        history = node.get_history(session, [prompt] + state["messages"])
        return [prompt] + history + state["messages"]

    return create_react_agent(
        # TODO: I think this will fail with google builtin tools
        model=node.get_chat_model(),
        tools=tools,
        prompt=prompt_callable,
    )


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
        "temp_state": state.get("temp_state", {}),
        "session_state": session.state or {},
    }
    return PromptTemplateContext(
        session,
        source_material_id=node.source_material_id,
        collection_id=node.collection_id,
        extra=extra_prompt_context,
    )


def _get_configured_tools(node, session: ExperimentSession, tool_callbacks: ToolCallbacks) -> list[dict | BaseTool]:
    """Get instantiated tools for the given node configuration."""
    tools = get_node_tools(node.django_node, session, tool_callbacks=tool_callbacks)
    tools.extend(node.get_llm_service().attach_built_in_tools(node.built_in_tools, node.tool_config))
    if node.collection_index_id:
        collection = Collection.objects.get(id=node.collection_index_id)
        tools.append(
            collection.get_search_tool(max_results=node.max_results, generate_citations=node.generate_citations)
        )

    if node.disabled_tools:
        # Model builtin tools doesn't have a name attribute and are dicts
        return [tool for tool in tools if hasattr(tool, "name") and tool.name not in node.disabled_tools]
    return tools

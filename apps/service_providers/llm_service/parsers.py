from langchain_core.messages import AIMessage

from apps.experiments.models import ExperimentSession
from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.service_providers.llm_service.utils import get_cited_files_from_text


def parse_output_for_anthropic(
    output: AIMessage | dict | str | list, session: ExperimentSession, include_citations: bool = True
) -> LlmChatResponse:
    """Parse Anthropic's output, resolving `<CIT n />` markers in the assembled text to the team's cited files."""
    response = _parse_content(output)
    if include_citations and session is not None:
        response.cited_files |= get_cited_files_from_text(response.text, session.team_id)
    return response


def _parse_content(output: AIMessage | dict | str | list) -> LlmChatResponse:
    """Parse Anthropic's output and append inline URL references to the text.

    Iterates through content blocks to find citations and append them to the corresponding text block while building
    the final output. Although Langchain exposes a .text attribute on the AIMessage object with the assembled text
    response from the LLM, we need to process content blocks manually to include inline citation links.
    """
    if isinstance(output, AIMessage):
        output = output.content

    if output is None or isinstance(output, str):
        return LlmChatResponse(text=output or "")

    if isinstance(output, list):
        chat_response = LlmChatResponse(text="")
        for item in output:
            chat_response += _parse_content(item)
        return chat_response

    if isinstance(output, dict):
        if "output" in output:
            return _parse_content(output["output"])
        elif output.get("type") == "text":
            text = output.get("text", "")
            for citation in output.get("citations", []):
                if citation.get("title") and citation.get("url"):
                    text += f" [{citation['title']}]({citation['url']})"
            return LlmChatResponse(text=text)
        else:
            return LlmChatResponse(text="")
    return LlmChatResponse(text="")

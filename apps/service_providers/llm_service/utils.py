import re
from io import BytesIO

import httpx
from langchain_core.messages import HumanMessage

from apps.experiments.models import ExperimentSession
from apps.files.models import File


def detangle_file_ids(file_ids: list[str]) -> list[str]:
    """
    There is a bug in the OpenAI API where separate file ids are sometimes returned concatenated together.

    Example:
        file-123Abcfile-123Def
    Should be parsed as:
        ['file-123Abc', 'file-123Def']
    """
    detangled_file_ids = []
    for file_id in file_ids:
        detangled_file_ids.extend(re.findall(r"file-(?:.*?)(?=file-|$)", file_id))
    return detangled_file_ids


def extract_file_ids_from_ocs_citations(text: str) -> list[int]:
    from apps.chat.agent.tools import OCS_CITATION_PATTERN

    file_ids = []
    for match in re.finditer(OCS_CITATION_PATTERN, text):
        file_ids.append(match.group("file_id"))
    return file_ids


def populate_reference_section_from_citations(text: str, cited_files: list[File], session: ExperimentSession) -> str:
    """
    Parse the AI message for citations in the format <CIT the-file-id /> to build a reference section at the end.
    Each citation is replaced with a footnote-style reference [^1], [^2], etc., and a reference section is
    appended at the end of the message with download links for each cited file.

    Example:

    Input:
    ```
    Here is a fact <CIT 123 />. Here is another fact <CIT 456 />.
    ```

    Output:
    ```
    Here is a fact [^1]. Here is another fact [^2].

    [^1]: [file_123.txt](http://example.com/file_123.txt)
    [^2]: [file_456.pdf](http://example.com/file_456.pdf)
    ```
    """
    from apps.chat.agent.tools import OCS_CITATION_PATTERN

    files = {file.id: file for file in cited_files}
    citation_pattern = re.compile(OCS_CITATION_PATTERN)
    tracked_file_ids = []
    file_references = {}  # Store references to avoid duplicates

    def replace_citations(match):
        """
        Replace a citation match with a footnote-style reference.
        The match is expected to be in the format <CIT 123 />
        """
        file_id = int(match.groupdict()["file_id"])
        if file_id not in files:
            # LLM hallucinated a file id. We don't want to show this to users, so remove the citation.
            return ""

        file = files[file_id]

        # Determine the citation index
        if file_id in tracked_file_ids:
            # If the file has already been cited, return the existing citation reference
            citation_index = tracked_file_ids.index(file_id) + 1
        else:
            citation_index = len(tracked_file_ids) + 1
            tracked_file_ids.append(file_id)

            # Store the reference for this file (only once)
            download_link = file.download_link(session.id)
            file_references[citation_index] = f"[^{citation_index}]: [{file.name}]({download_link})"

        return f"[^{citation_index}]"

    # Replace citations in the message
    text = citation_pattern.sub(replace_citations, text)

    # Build reference section from stored references
    if file_references:
        refs_section = "\n".join([file_references[i] for i in sorted(file_references.keys())])
        text += "\n\n" + refs_section

    return text


def remove_citations_from_text(text: str) -> str:
    """
    Remove all citations from the text.

    Example:
        >>> remove_citations_from_text("This is a fact <CIT 123 />.")
        'This is a fact .'
    """
    # This is used as a cleanup step to prevent users from tricking the bot to generate citations.
    # While participants will not be able to download or really do anything with the citations, it will still reveal
    # file ids
    from apps.chat.agent.tools import OCS_CITATION_PATTERN

    citation_pattern = re.compile(OCS_CITATION_PATTERN)
    return citation_pattern.sub("", text)


def get_openai_container_file_contents(
    container_id: str, openai_file_id: str, openai_api_key: str, openai_organization: str | None = None
) -> BytesIO:
    # TODO: use the OpenAI Python client library when it supports container files
    headers = {"Authorization": f"Bearer {openai_api_key}", "OpenAI-Organization": openai_organization or ""}
    url = f"https://api.openai.com/v1/containers/{container_id}/files/{openai_file_id}/content"

    with httpx.stream("GET", url, headers=headers, timeout=30) as response:
        response.raise_for_status()
        return BytesIO(response.read())


def format_multimodal_input(message: str, attachments: list) -> HumanMessage:
    parts = [{"type": "text", "text": message}]
    for att in attachments:
        download_url = att.download_link
        mime_type = att.content_type or ""
        parts.append(
            {
                "type": "image" if mime_type.startswith("image/") else "file",
                "source_type": "url",
                "url": download_url,
                "mime_type": mime_type,
            }
        )
    return HumanMessage(content=parts)

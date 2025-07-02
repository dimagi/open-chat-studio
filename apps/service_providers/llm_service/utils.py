import re


def detangle_file_ids(file_ids: str) -> list[str]:
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

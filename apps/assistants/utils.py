from collections.abc import Generator

from apps.service_providers.models import LlmProviderTypes


def get_llm_providers_for_assistants(team):
    return team.llmprovider_set.filter(type=LlmProviderTypes.openai)


def get_assistant_tool_options():
    return [
        ("code_interpreter", "Code Interpreter"),
        ("file_search", "File Search"),
    ]


def chunk_list(list_: list, chunk_size: int) -> Generator[list]:
    for i in range(0, len(list_), chunk_size):
        yield list_[i : i + chunk_size]

import openai
from openai.types.beta import Assistant

from apps.assistants.models import OpenAiAssistant
from apps.assistants.utils import get_assistant_tool_options
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team


def push_assistant_to_openai(assistant: OpenAiAssistant):
    """Pushes the assistant to OpenAI. If the assistant already exists, it will be updated."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    if assistant.assistant_id:
        client.beta.assistants.update(assistant.assistant_id, **_ocs_assistant_to_openai_kwargs(assistant))
    else:
        openai_assistant = client.beta.assistants.create(**_ocs_assistant_to_openai_kwargs(assistant))
        assistant.assistant_id = openai_assistant.id
        assistant.save()


def sync_from_openid(assistant: OpenAiAssistant):
    """Syncs the local assistant instance with the remote OpenAI assistant."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant.assistant_id)
    for key, value in _openai_assistant_to_ocs_kwargs(openai_assistant).items():
        setattr(assistant, key, value)
    assistant.save()


def import_openai_assistant(assistant_id: str, llm_provider: LlmProvider, team: Team) -> OpenAiAssistant:
    client = llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant_id)
    kwargs = _openai_assistant_to_ocs_kwargs(openai_assistant, team=team, llm_provider=llm_provider)
    assistant = OpenAiAssistant.objects.create(**kwargs)
    return assistant


def delete_openai_assistant(assistant: OpenAiAssistant) -> bool:
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    try:
        client.beta.assistants.delete(assistant.assistant_id)
        return True
    except openai.NotFoundError:
        return False


def _ocs_assistant_to_openai_kwargs(assistant: OpenAiAssistant) -> dict:
    return {
        "instructions": assistant.instructions,
        "name": assistant.name,
        "tools": assistant.formatted_tools,
        "model": assistant.llm_model,
        "metadata": {
            "ocs_assistant_id": assistant.id,
        },
    }


def _openai_assistant_to_ocs_kwargs(assistant: Assistant, team=None, llm_provider=None) -> dict:
    builtin_tools = dict(get_assistant_tool_options())
    kwargs = {
        "assistant_id": assistant.id,
        "name": assistant.name,
        "instructions": assistant.instructions,
        "builtin_tools": [tool.type for tool in assistant.tools if tool.type in builtin_tools],
        # What if the model isn't one of the ones configured for the LLM Provider?
        "llm_model": assistant.model,
    }
    if team:
        kwargs["team"] = team
    if llm_provider:
        kwargs["llm_provider"] = llm_provider
    return kwargs

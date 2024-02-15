import mimetypes
import pathlib
from functools import wraps
from io import BytesIO

import openai
from openai.types.beta import Assistant

from apps.assistants.models import OpenAiAssistant
from apps.assistants.utils import get_assistant_tool_options
from apps.files.models import File
from apps.service_providers.models import LlmProvider
from apps.teams.models import Team


class OpenAiSyncError(Exception):
    pass


def wrap_openai_errors(fn):
    @wraps(fn)
    def _inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except openai.APIError as e:
            message = e.message
            if isinstance(e.body, dict):
                try:
                    message = e.body["message"]
                except KeyError | AttributeError:
                    pass

            raise OpenAiSyncError(message) from e

    return _inner


@wrap_openai_errors
def push_assistant_to_openai(assistant: OpenAiAssistant):
    """Pushes the assistant to OpenAI. If the assistant already exists, it will be updated."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    if assistant.assistant_id:
        client.beta.assistants.update(assistant.assistant_id, **_ocs_assistant_to_openai_kwargs(assistant))
    else:
        openai_assistant = client.beta.assistants.create(**_ocs_assistant_to_openai_kwargs(assistant))
        assistant.assistant_id = openai_assistant.id
        assistant.save()


@wrap_openai_errors
def push_file_to_openai(assistant: OpenAiAssistant, file: File):
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    with file.file.open("rb") as fh:
        bytesio = BytesIO(fh.read())
    openai_file = client.files.create(
        file=(file.name, bytesio),
        purpose="assistants",
    )
    file.external_id = openai_file.id
    file.external_source = "openai"
    file.save()


@wrap_openai_errors
def delete_file_from_openai(assistant: OpenAiAssistant, file: File):
    if not file.external_id or file.external_source != "openai":
        return

    client = assistant.llm_provider.get_llm_service().get_raw_client()
    client.beta.assistants.files.delete(assistant_id=assistant.assistant_id, file_id=file.external_id)
    try:
        client.files.delete(file.external_id)
    except openai.NotFoundError:
        pass
    file.external_id = ""
    file.external_source = ""


@wrap_openai_errors
def fetch_file_from_openai(assistant: OpenAiAssistant, file_id: str) -> File:
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    openai_file = client.files.retrieve(file_id)
    filename = openai_file.filename
    try:
        filename = pathlib.Path(openai_file.filename).name
    except Exception:
        pass

    content_type = mimetypes.guess_type(filename)[0]
    file = File(
        team=assistant.team,
        name=filename,
        content_type=content_type,
        external_id=openai_file.id,
        external_source="openai",
    )
    # Can't retrieve content from openai assistant files
    # content = client.files.retrieve_content(openai_file.id)
    # file.file.save(filename, ContentFile(content.read()))
    file.save()
    return file


@wrap_openai_errors
def sync_from_openai(assistant: OpenAiAssistant):
    """Syncs the local assistant instance with the remote OpenAI assistant."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant.assistant_id)
    for key, value in _openai_assistant_to_ocs_kwargs(openai_assistant).items():
        setattr(assistant, key, value)
    assistant.save()
    sync_files_from_openai(openai_assistant, assistant)


@wrap_openai_errors
def sync_files_from_openai(openai_assistant: Assistant, assistant: OpenAiAssistant):
    existing_files = {file.external_id: file for file in assistant.files.all() if file.external_id}
    if openai_assistant.file_ids:
        for file_id in openai_assistant.file_ids:
            try:
                existing_files.pop(file_id)
            except KeyError:
                assistant.files.add(fetch_file_from_openai(assistant, file_id))
    File.objects.filter(id__in=[file.id for file in existing_files.values()]).delete()


@wrap_openai_errors
def import_openai_assistant(assistant_id: str, llm_provider: LlmProvider, team: Team) -> OpenAiAssistant:
    client = llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant_id)
    kwargs = _openai_assistant_to_ocs_kwargs(openai_assistant, team=team, llm_provider=llm_provider)
    assistant = OpenAiAssistant.objects.create(**kwargs)
    sync_files_from_openai(openai_assistant, assistant)
    return assistant


@wrap_openai_errors
def delete_openai_assistant(assistant: OpenAiAssistant):
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    try:
        client.beta.assistants.delete(assistant.assistant_id)
    except openai.NotFoundError:
        pass

    for file in assistant.files.all():
        delete_file_from_openai(assistant, file)


def _ocs_assistant_to_openai_kwargs(assistant: OpenAiAssistant) -> dict:
    file_ids = []
    for file in assistant.files.all():
        if not file.external_id:
            push_file_to_openai(assistant, file)
        file_ids.append(file.external_id)

    return {
        "instructions": assistant.instructions,
        "name": assistant.name,
        "tools": assistant.formatted_tools,
        "model": assistant.llm_model,
        "file_ids": file_ids,
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

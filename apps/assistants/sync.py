import mimetypes
import pathlib
from functools import wraps
from io import BytesIO

import openai
from openai import OpenAI
from openai.types.beta import Assistant

from apps.assistants.models import OpenAiAssistant, ToolResources
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
        try:
            vector_store_id = openai_assistant.tool_resources.file_search.vector_store_ids[0]
        except Exception:
            pass
        else:
            resource = assistant.tool_resources.get(tool_type="file_search")
            resource.extra["vector_store_id"] = vector_store_id
            resource.save()
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
def delete_file_from_vector_store(client, vector_store_id, file):
    if not file.external_id or file.external_source != "openai":
        return

    try:
        client.resources.beta.vector_stores.retrieve(vector_store_id)
    except openai.NotFoundError:
        pass
    else:
        client.resources.beta.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file.external_id)


@wrap_openai_errors
def delete_file_from_openai(client: OpenAI, file: File):
    if not file.external_id or file.external_source != "openai":
        return

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
    sync_tool_resources(openai_assistant, assistant)


@wrap_openai_errors
def sync_tool_resources(openai_assistant: Assistant, assistant: OpenAiAssistant):
    if not openai_assistant.tool_resources:
        return

    code_interpreter = openai_assistant.tool_resources.code_interpreter
    if code_interpreter and code_interpreter.file_ids:
        ocs_code_interpreter, created = ToolResources.objects.get_or_create(
            assistant=assistant, tool_type="code_interpreter"
        )
        sync_tool_resource_files_from_openai(code_interpreter.file_ids, ocs_code_interpreter)
    else:
        ToolResources.objects.filter(assistant=assistant, tool_type="code_interpreter").delete()

    file_search = openai_assistant.tool_resources.file_search
    if file_search and file_search.vector_store_ids:
        ocs_file_search, created = ToolResources.objects.get_or_create(assistant=assistant, tool_type="file_search")
        vector_store_id = file_search.vector_store_ids[0]
        if ocs_file_search.extra.get("vector_store_id") != vector_store_id:
            ocs_file_search.extra["vector_store_id"] = vector_store_id
            ocs_file_search.save()

        client = assistant.llm_provider.get_llm_service().get_raw_client()
        file_ids = (
            file.id
            for file in client.beta.vector_stores.files.list(
                vector_store_id=vector_store_id  # there can only be one
            )
        )
        sync_tool_resource_files_from_openai(file_ids, ocs_file_search)
    else:
        ToolResources.objects.filter(assistant=assistant, tool_type="file_search").delete()


def sync_tool_resource_files_from_openai(file_ids, ocs_resource):
    resource_files = ocs_resource.files.all()
    unused_files = {file.id for file in resource_files}
    existing_files = {file.external_id: file for file in resource_files if file.external_id}
    for file_id in file_ids:
        try:
            file = existing_files.pop(file_id)
            unused_files.remove(file.id)
        except KeyError:
            ocs_resource.files.add(fetch_file_from_openai(ocs_resource.assistant, file_id))
    File.objects.filter(id__in=unused_files).delete()


def sync_vector_store_files_to_openai(client, vector_store_id, files_ids: list[str]):
    vector_store_files = (file.id for file in client.beta.vector_stores.files.list(vector_store_id=vector_store_id))
    to_delete_remote = []
    for file_id in vector_store_files:
        try:
            files_ids.remove(file_id)
        except ValueError:
            to_delete_remote.append(file_id)

    for file_id in to_delete_remote:
        client.beta.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)

    if files_ids:
        client.beta.vector_stores.file_batches.create(vector_store_id=vector_store_id, file_ids=files_ids)


@wrap_openai_errors
def import_openai_assistant(assistant_id: str, llm_provider: LlmProvider, team: Team) -> OpenAiAssistant:
    client = llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant_id)
    kwargs = _openai_assistant_to_ocs_kwargs(openai_assistant, team=team, llm_provider=llm_provider)
    assistant = OpenAiAssistant.objects.create(**kwargs)
    sync_tool_resources(openai_assistant, assistant)
    return assistant


@wrap_openai_errors
def delete_openai_assistant(assistant: OpenAiAssistant):
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    try:
        client.beta.assistants.delete(assistant.assistant_id)
    except openai.NotFoundError:
        pass

    for resource in assistant.tool_resources.all():
        if resource.tool_type == "file_search" and "vector_store_id" in resource.extra:
            vector_store_id = resource.extra.pop("vector_store_id")
            client.beta.vector_stores.delete(vector_store_id=vector_store_id)

        for file in resource.files.all():
            delete_file_from_openai(client, resource, file)


def _ocs_assistant_to_openai_kwargs(assistant: OpenAiAssistant) -> dict:
    """Note: this has some side effects of syncing files and vector stores"""

    data = {
        "instructions": assistant.instructions,
        "name": assistant.name,
        "tools": assistant.formatted_tools,
        "model": assistant.llm_model,
        "temperature": assistant.temperature,
        "top_p": assistant.top_p,
        "metadata": {
            "ocs_assistant_id": str(assistant.id),
        },
        "tool_resources": {},
    }

    resources = {resource.tool_type: resource for resource in assistant.tool_resources.all()}
    if "code_interpreter" in assistant.builtin_tools and (code_interpreter := resources.get("code_interpreter")):
        file_ids = create_files_remote(assistant, code_interpreter.files.all())
        data["tool_resources"]["code_interpreter"] = {"file_ids": file_ids}

    if "file_search" in assistant.builtin_tools and (file_search := resources.get("file_search")):
        file_ids = create_files_remote(assistant, file_search.files.all())
        vector_store_id = file_search.extra.get("vector_store_id")
        client = assistant.llm_provider.get_llm_service().get_raw_client()
        vector_store_id = update_or_create_vector_store(
            client, f"{assistant.name} - File Search", vector_store_id, file_ids
        )
        data["tool_resources"]["file_search"] = {"vector_store_ids": [vector_store_id]}

    return data


@wrap_openai_errors
def update_or_create_vector_store(client, name, vector_store_id, file_ids) -> str:
    if vector_store_id:
        try:
            client.beta.vector_stores.retrieve(vector_store_id)
        except openai.NotFoundError:
            pass
        else:
            sync_vector_store_files_to_openai(client, vector_store_id, file_ids)
            return vector_store_id

    vector_store = client.beta.vector_stores.create(name=name, file_ids=file_ids)
    return vector_store.id


def _openai_assistant_to_ocs_kwargs(assistant: Assistant, team=None, llm_provider=None) -> dict:
    builtin_tools = dict(get_assistant_tool_options())
    kwargs = {
        "assistant_id": assistant.id,
        "name": assistant.name or "Untitled Assistant",
        "instructions": assistant.instructions or "",
        "builtin_tools": [tool.type for tool in assistant.tools if tool.type in builtin_tools],
        # What if the model isn't one of the ones configured for the LLM Provider?
        "llm_model": assistant.model,
        "temperature": assistant.temperature,
        "top_p": assistant.top_p,
    }
    if team:
        kwargs["team"] = team
    if llm_provider:
        kwargs["llm_provider"] = llm_provider
    return kwargs


def create_files_remote(assistant, files):
    file_ids = []
    for file in files:
        if not file.external_id:
            push_file_to_openai(assistant, file)
        file_ids.append(file.external_id)
    return file_ids

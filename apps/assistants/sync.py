"""
### A Brief overview of how Assistants, tool resources and threads play together.

An Assistant can use two built-in tools to operate on files, namely "File Search" and "Code Interpreter".

File Search:
    Uploaded files are embedded and stored in a vector store. This vector store is then attached to the
    "file_search" tool resource, which in turn is attached to an assistant.

Code Interpreter:
    Uploaded files are attached directly to the "code_interpreter" tool resource which n turn is attached to the
    assistant.


## Something like this
Assistant
    |______tool_resources
                |__________code_interpreter
                |               |__________file1
                |               |__________file2
                |
                |__________file_search
                                |__________vector_store1
                                |               |_________file1
                                |               |_________file2
                                |
                                |__________vector_store1


These resources are available globally within the scope of the assistant. Any user interacting with the assistant
will have access to these files.

### Threads
Each chat session with the assistant occurs within a thread. Threads can also utilize these global resources or
create its own resources (code_interpreter / file_search ), scoped to the current thread. This means that other
threads or chats with the assistant will not have access to the files uploaded to a specific thread's resources.

Assistant
    |________Thread1
                |______tool_resources
                            |__________code_interpreter
                            |               |__________file1
                            |               |__________file2
                            |
                            |__________file_search
                                            |__________vector_store1
                                            |               |_________file1
                                            |               |_________file2
                                            |
                                            |__________vector_store1

Note that a thread-level tool resource will only function if it is enabled at the assistant level. For example,
the file search tool will not be available within a thread unless it is enabled at the assistant level.
Once enabled on the assistant, the thread can add its own files to its vector store(s) and interact with those
files, as well as those provided by the assistant.
"""

import mimetypes
import pathlib
from functools import wraps
from io import BytesIO
from tempfile import TemporaryFile

import magic
import openai
from django.core.files import File as DjangoFile
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAI
from openai.types.beta import Assistant

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.assistants.utils import get_assistant_tool_options
from apps.files.models import File
from apps.service_providers.models import LlmProvider, LlmProviderModel, LlmProviderTypes
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
def push_assistant_to_openai(assistant: OpenAiAssistant, internal_tools: list | None = None):
    """Pushes the assistant to OpenAI. If the assistant already exists, it will be updated."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    data = _ocs_assistant_to_openai_kwargs(assistant)
    data["tool_resources"] = _sync_tool_resources(assistant)

    if internal_tools:
        data["tools"] = [convert_to_openai_tool(tool, strict=True) for tool in internal_tools]

    if assistant.assistant_id:
        client.beta.assistants.update(assistant.assistant_id, **data)
    else:
        openai_assistant = client.beta.assistants.create(**data)
        assistant.assistant_id = openai_assistant.id
        assistant.save()


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
def sync_from_openai(assistant: OpenAiAssistant):
    """Syncs the local assistant instance with the remote OpenAI assistant."""
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant.assistant_id)
    for key, value in _openai_assistant_to_ocs_kwargs(openai_assistant, team=assistant.team).items():
        setattr(assistant, key, value)
    assistant.save()
    _sync_tool_resources_from_openai(openai_assistant, assistant)


@wrap_openai_errors
def import_openai_assistant(assistant_id: str, llm_provider: LlmProvider, team: Team) -> OpenAiAssistant:
    client = llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant_id)
    kwargs = _openai_assistant_to_ocs_kwargs(openai_assistant, team=team, llm_provider=llm_provider)
    assistant = OpenAiAssistant.objects.create(**kwargs)
    _sync_tool_resources_from_openai(openai_assistant, assistant)
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
            delete_file_from_openai(client, file)


def is_tool_configured_remotely_but_missing_locally(assistant_data, local_tool_types, tool_name: str) -> bool:
    """Checks if a tool is configured in OpenAI but missing in OCS."""
    tool_configured_in_openai = hasattr(assistant_data.tool_resources, tool_name) and getattr(
        assistant_data.tool_resources, tool_name
    )
    return tool_configured_in_openai and tool_name not in local_tool_types


@wrap_openai_errors
def get_out_of_sync_files(assistant: OpenAiAssistant) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Checks if the files for an assistant in OCS match the files in OpenAI."""
    tool_resources = assistant.tool_resources.all()
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    assistant_data = client.beta.assistants.retrieve(assistant.assistant_id)

    files_missing_local = {}
    files_missing_remote = {}
    local_tool_types = {resource.tool_type: resource for resource in tool_resources}
    if is_tool_configured_remotely_but_missing_locally(assistant_data, local_tool_types, "code_interpreter"):
        openai_file_ids = _get_tool_file_ids_from_openai(client, assistant_data, "code_interpreter")
        if openai_file_ids:
            files_missing_local["code_interpreter"] = openai_file_ids
    if is_tool_configured_remotely_but_missing_locally(assistant_data, local_tool_types, "file_search"):
        openai_file_ids = _get_tool_file_ids_from_openai(client, assistant_data, "file_search")
        files_missing_local["file_search"] = openai_file_ids

    # ensure files match
    for resource in tool_resources:
        openai_file_ids = _get_tool_file_ids_from_openai(client, assistant_data, resource.tool_type)
        ocs_file_ids = [file.external_id for file in resource.files.all() if file.external_id]
        if missing := set(openai_file_ids) - set(ocs_file_ids):
            files_missing_local[resource.tool_type] = list(missing)
        if extra := set(ocs_file_ids) - set(openai_file_ids):
            files_missing_remote[resource.tool_type] = list(extra)
    return files_missing_local, files_missing_remote


def _get_tool_file_ids_from_openai(client, assistant_data, tool_type: str) -> list[str]:
    """
    Retrieve file IDs from OpenAI based on the specified tool resource type.

    Args:
        client: The OpenAI client instance used to interact with the OpenAI API.
        assistant_data: The assistant data containing tool resources.
        tool_type: The type of tool resource to retrieve file IDs for.

    Returns:
        list[str]: A list of file IDs retrieved from OpenAI.

    The function handles two types of tool resources:
    - "code_interpreter": Returns file IDs directly from the code interpreter tool resource if available.
    - "file_search": Retrieves file IDs from the OpenAI vector store using the provided vector store ID.
    """
    if tool_type == "code_interpreter":
        code_interpreter = getattr(assistant_data.tool_resources, "code_interpreter", None)
        if code_interpreter is not None and code_interpreter.file_ids:
            return code_interpreter.file_ids
        return []

    if tool_type == "file_search":
        vector_store_ids = assistant_data.tool_resources.file_search.vector_store_ids
        if not vector_store_ids:
            return []
        return [file.id for file in client.beta.vector_stores.files.list(vector_store_id=vector_store_ids[0])]


@wrap_openai_errors
def is_synced_with_openai(assistant: OpenAiAssistant) -> list[str]:
    """Checks if the assistant in OCS is in sync with the assistant in OpenAI."""

    diffs = []
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    openai_assistant = client.beta.assistants.retrieve(assistant.assistant_id)
    for key, value in _openai_assistant_to_ocs_kwargs(openai_assistant).items():
        current_value = getattr(assistant, key, None)
        if current_value != value:
            diffs.append(key)

    tool_resources = assistant.tool_resources.all()

    local_tool_resources = {resource.tool_type: resource for resource in tool_resources}
    if is_tool_configured_remotely_but_missing_locally(openai_assistant, local_tool_resources, "code_interpreter"):
        diffs.append("code_interpreter")
    if is_tool_configured_remotely_but_missing_locally(openai_assistant, local_tool_resources, "file_search"):
        diffs.append("file_search")

    if local_tool := local_tool_resources.get("file_search"):
        vector_store_ids = openai_assistant.tool_resources.file_search.vector_store_ids
        if [local_tool.extra.get("vector_store_id")] != vector_store_ids:
            diffs.append("File search vector store ID")

    return diffs


def _fetch_file_from_openai(assistant: OpenAiAssistant, file_id: str) -> File:
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


def _sync_tool_resources_from_openai(openai_assistant: Assistant, assistant: OpenAiAssistant):
    tools = {tool.type for tool in openai_assistant.tools}
    if "code_interpreter" in tools:
        ocs_code_interpreter, _ = ToolResources.objects.get_or_create(assistant=assistant, tool_type="code_interpreter")
        try:
            code_file_ids = openai_assistant.tool_resources.code_interpreter.file_ids
        except AttributeError:
            pass
        else:
            _sync_tool_resource_files_from_openai(code_file_ids, ocs_code_interpreter)

    if "file_search" in tools:
        ocs_file_search, _ = ToolResources.objects.get_or_create(assistant=assistant, tool_type="file_search")
        try:
            vector_store_ids = openai_assistant.tool_resources.file_search.vector_store_ids
            if not vector_store_ids:
                # OpenAI doesn't create a vector store when you create an assistant through their UI and enable
                # file search with no files in it, so let's not try to fetch it
                return
            vector_store_id = vector_store_ids[0]
        except AttributeError:
            pass
        else:
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
            _sync_tool_resource_files_from_openai(file_ids, ocs_file_search)


def _sync_tool_resource_files_from_openai(file_ids, ocs_resource):
    resource_files = ocs_resource.files.all()
    unused_files = {file.id for file in resource_files}
    existing_files = {file.external_id: file for file in resource_files if file.external_id}
    for file_id in file_ids:
        try:
            file = existing_files.pop(file_id)
            unused_files.remove(file.id)
        except KeyError:
            ocs_resource.files.add(_fetch_file_from_openai(ocs_resource.assistant, file_id))
    if unused_files:
        File.objects.filter(id__in=unused_files).delete()


def _sync_vector_store_files_to_openai(client, vector_store_id, files_ids: list[str]):
    kwargs = {}
    to_delete_remote = []

    while True:
        vector_store_files = client.beta.vector_stores.files.list(
            order="asc",
            vector_store_id=vector_store_id,
            **kwargs,
        )
        for v_file in vector_store_files.data:
            try:
                files_ids.remove(v_file.id)
            except ValueError:
                to_delete_remote.append(v_file.id)

        if not vector_store_files.has_more:
            break
        kwargs["after"] = vector_store_files.last_id

    for file_id in to_delete_remote:
        client.beta.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)

    if files_ids:
        client.beta.vector_stores.file_batches.create(vector_store_id=vector_store_id, file_ids=files_ids)


def _ocs_assistant_to_openai_kwargs(assistant: OpenAiAssistant) -> dict:
    return {
        "instructions": assistant.instructions,
        "name": assistant.name,
        "tools": assistant.formatted_tools,
        "model": assistant.llm_provider_model.name,
        "temperature": assistant.temperature,
        "top_p": assistant.top_p,
        "metadata": {
            "ocs_assistant_id": str(assistant.id),
        },
        "tool_resources": {},
    }


def _sync_tool_resources(assistant):
    resource_data = {}
    resources = {resource.tool_type: resource for resource in assistant.tool_resources.all()}
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    if code_interpreter := resources.get("code_interpreter"):
        file_ids = create_files_remote(client, code_interpreter.files.all())
        resource_data["code_interpreter"] = {"file_ids": file_ids}

    if file_search := resources.get("file_search"):
        file_ids = create_files_remote(client, file_search.files.all())
        store_id = file_search.extra.get("vector_store_id")
        updated_store_id = _update_or_create_vector_store(
            assistant, f"{assistant.name} - File Search", store_id, file_ids
        )
        if store_id != updated_store_id:
            file_search.extra["vector_store_id"] = updated_store_id
            file_search.save()
        resource_data["file_search"] = {"vector_store_ids": [updated_store_id]}

    return resource_data


def _update_or_create_vector_store(assistant, name, vector_store_id, file_ids) -> str:
    client = assistant.llm_provider.get_llm_service().get_raw_client()
    if vector_store_id:
        try:
            client.beta.vector_stores.retrieve(vector_store_id)
        except openai.NotFoundError:
            vector_store_id = None

    if not vector_store_id and assistant.assistant_id:
        # check if there is a vector store attached to this assistant that we don't know about
        openai_assistant = client.beta.assistants.retrieve(assistant.assistant_id)
        try:
            vector_store_id = openai_assistant.tool_resources.file_search.vector_store_ids[0]
        except (AttributeError, IndexError):
            pass

    if vector_store_id:
        _sync_vector_store_files_to_openai(client, vector_store_id, file_ids)
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
        "temperature": assistant.temperature,
        "top_p": assistant.top_p,
    }
    if team:
        kwargs["team"] = team
        # If the model doesn't exist when syncing, create a new one
        llm_provider_model, _ = LlmProviderModel.objects.get_or_create_for_team(
            team=team,
            type=str(LlmProviderTypes.openai),
            name=assistant.model,
        )
        kwargs["llm_provider_model"] = llm_provider_model
    if llm_provider:
        kwargs["llm_provider"] = llm_provider
    return kwargs


def create_files_remote(client, files):
    file_ids = []
    for file in files:
        if not file.external_id:
            _push_file_to_openai(client, file)
        file_ids.append(file.external_id)
    return file_ids


def _push_file_to_openai(client: OpenAiAssistant, file: File):
    with file.file.open("rb") as fh:
        bytesio = BytesIO(fh.read())
    openai_file = client.files.create(
        file=(file.name, bytesio),
        purpose="assistants",
    )
    file.external_id = openai_file.id
    file.external_source = "openai"
    file.save()


def get_and_store_openai_file(client, file_id: str, team_id: int) -> File:
    """Retrieve the content of the openai file with id=`file_id` and create a new `File` instance"""
    file = client.files.retrieve(file_id)
    filename = file.filename
    try:
        filename = pathlib.Path(file.filename).name
    except Exception:
        pass

    file_content_obj = client.files.content(file_id)
    file_content_bytes = file_content_obj.read()

    content_type = mimetypes.guess_type(filename)[0]
    if not content_type:
        # typically means the filename doesn't have an extension
        content_type = magic.from_buffer(file_content_bytes, mime=True)
        extension = mimetypes.guess_extension(content_type)
        # leading '.' is included
        filename = f"{filename}{extension}"

    with TemporaryFile(mode="w+b") as file:
        file.write(file_content_bytes)
        django_file = DjangoFile(file, name=filename)
        return File.objects.create(
            name=filename,
            file=django_file,
            external_id=file_id,
            team_id=team_id,
            content_type=content_type,
            content_size=django_file.size,
        )

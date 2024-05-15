from django.core.files.base import ContentFile


def migrate_assistant_to_v2(assistant, apps=None):
    """This only migrates the internal DB model. It does not sync with OpenAI"""
    if apps:
        ToolResources = apps.get_model("assistants.ToolResources")
    else:
        from apps.assistants.models import ToolResources

    builtin_tools = assistant.builtin_tools
    if "retrieval" in builtin_tools:
        builtin_tools.remove("retrieval")
        builtin_tools.append("file_search")

    assistant.save()

    for tool in builtin_tools:
        resource, created = ToolResources.objects.get_or_create(tool_type=tool, assistant=assistant)
        if created:
            # copy the file so that it is not shared between resources
            files = [_duplicate_file(file, apps) for file in assistant.files.all()]
            resource.files.set(files)

    assistant.files.all().delete()


def _duplicate_file(file, apps=None):
    if apps:
        File = apps.get_model("files.File")
    else:
        from apps.files.models import File

    new_file = File(
        name=file.name,
        external_source=file.external_source,
        external_id=file.external_id,
        content_size=file.content_size,
        content_type=file.content_type,
        schema=file.schema,
        team=file.team,
    )
    if file.file:
        new_file_file = ContentFile(file.file.read())
        new_file_file.name = file.file.name
        new_file.file = new_file_file
    new_file.save()
    return new_file

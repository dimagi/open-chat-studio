def migrate_assistant_to_v2(assistant, tool_resources_cls=None):
    """This only migrates the internal DB model. It does not sync with OpenAI"""
    if not tool_resources_cls:
        from apps.assistants.models import ToolResources
        tool_resources_cls = ToolResources

    builtin_tools = assistant.builtin_tools
    if "retrieval" in builtin_tools:
        builtin_tools.remove("retrieval")
        builtin_tools.append("file_search")

    assistant.save()

    files = assistant.files.all()
    for tool in builtin_tools:
        resource, created = tool_resources_cls.objects.get_or_create(tool_type=tool, assistant=assistant)
        if created:
            resource.files.set(files)

    assistant.files.clear()

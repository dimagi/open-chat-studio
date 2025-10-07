"""
This is a temporary test for the fix_vector_store_duplication command. This test should be removed once the command
is not needed anymore.
"""

from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command

from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
@pytest.mark.parametrize("args", [["--assistant", "a-123"], ["--team", "assistant-team"]])
@patch("apps.experiments.management.commands.fix_vector_store_duplication._clear_assistant_vector_store", Mock())
@patch("apps.experiments.management.commands.fix_vector_store_duplication.push_assistant_to_openai")
def test_fix_vector_store_duplication_command(push_assistant_to_openai, args):
    assistant = OpenAiAssistantFactory(assistant_id="a-123", version_number=2, team__slug="assistant-team")
    original_tool_resource = assistant.tool_resources.create(
        tool_type="file_search", extra={"vector_store_id": "v-123"}
    )
    original_file = FileFactory(external_id="f-123", external_source="openai")
    original_tool_resource.files.add(original_file)

    # Set up a broken version with the same tool resource and file details as the working assistant version
    assistant_version = OpenAiAssistantFactory(assistant_id="a-312", version_number=1, working_version=assistant)
    version_tool_resource = assistant_version.tool_resources.create(
        tool_type="file_search", extra={"vector_store_id": "v-123"}
    )
    version_file = FileFactory(external_id="f-123", external_source="openai")
    version_tool_resource.files.add(version_file)

    call_command("fix_vector_store_duplication", *args)

    # Ensure the original version is in tact
    original_tool_resource.refresh_from_db()
    assert original_tool_resource.extra["vector_store_id"] == "v-123"
    assert original_tool_resource.files.first().external_id == "f-123"

    # Ensure the versioned resouce + files are cleared correctly
    assistant = push_assistant_to_openai.call_args[0][0]
    # A working version must not be updated
    assert assistant.is_a_version

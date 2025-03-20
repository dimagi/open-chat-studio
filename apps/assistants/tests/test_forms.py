# apps/assistants/tests/test_forms.py
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.assistants.forms import ToolResourceFileFormsets
from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.files.models import File
from apps.teams.models import Team


@pytest.mark.django_db()
def test_tool_resource_formsets_create_with_allow_file_downloads(db):
    """Test creating ToolResources during assistant creation with allow_file_downloads from assistant."""
    # Setup
    team = Team.objects.create(name="Test Team", slug="dimagi-test")
    assistant = OpenAiAssistant.objects.create(
        team=team,
        name="Test Assistant",
        builtin_tools=["code_interpreter", "file_search"],
        allow_file_downloads=True,  # Set the field directly
    )
    # Use SimpleUploadedFile to create a mock file
    file_content = b"Test file content"
    file = File.objects.create(team=team, name="test-file.txt", file=SimpleUploadedFile("test-file.txt", file_content))

    # Mock get_file_formset to return a simple formset
    mock_formset = MagicMock()
    mock_formset.is_valid.return_value = True
    mock_formset.save.return_value = [file]  # Simulate saving a file
    with patch("apps.assistants.forms.get_file_formset", return_value=mock_formset):
        # Create a minimal mock request object
        mock_request = SimpleNamespace(
            method="POST",
            POST={},  # No need for allow_file_downloads data
        )

        # Create the formsets instance with instance=None (create mode)
        formsets = ToolResourceFileFormsets(mock_request, instance=None)

        # Validate (should pass as formsets are mocked)
        assert formsets.is_valid()

        # Save
        formsets.save(mock_request, assistant)

        # Verify ToolResources were created
        code_interpreter_resource = ToolResources.objects.filter(
            assistant=assistant, tool_type="code_interpreter"
        ).first()
        file_search_resource = ToolResources.objects.filter(assistant=assistant, tool_type="file_search").first()

        assert code_interpreter_resource is not None
        assert file_search_resource is not None
        assert code_interpreter_resource.files.count() == 1
        assert file_search_resource.files.count() == 1

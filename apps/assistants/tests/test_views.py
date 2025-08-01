from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.assistants.models import ToolResources
from apps.documents.models import CollectionFile
from apps.files.models import File
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
class TestDeleteFileFromAssistant:
    @pytest.fixture()
    def assistant(self, team_with_users):
        return OpenAiAssistantFactory(team=team_with_users, builtin_tools=["code_interpreter"])

    @pytest.fixture()
    def resource(self, assistant):
        return ToolResources.objects.create(
            assistant=assistant, tool_type="code_interpreter", extra={"vector_store_id": "vs-123"}
        )

    @patch("apps.assistants.sync.OpenAIRemoteIndexManager.delete_file_from_index")
    def test_delete_file_removes_relationship_and_keeps_file_when_used_elsewhere(
        self, delete_file, assistant, resource, client
    ):
        """Test that file relationship is removed but file is kept when used in other resources."""
        team = assistant.team
        client.force_login(team.members.first())
        file = FileFactory(team=team, external_id="file_123", external_source="openai")

        collection = CollectionFactory(team=team)
        collection.files.add(file)

        # Setup: Add file to resource
        resource.files.add(file)

        # Create request
        response = client.delete(
            reverse("assistants:remove_file", args=[team.slug, assistant.id, resource.id, file.id])
        )

        # Assertions
        assert response.status_code == 200
        assert not resource.files.filter(id=file.id).exists()
        assert File.objects.filter(id=file.id).exists()
        assert CollectionFile.objects.filter(file_id=file.id).exists()  # File is preserved on the collection

        delete_file.assert_called_once_with(file_id="file_123")

    @patch("apps.assistants.sync.delete_file_from_openai")
    @patch("apps.assistants.sync.OpenAIRemoteIndexManager.delete_files_from_index")
    def test_delete_file_removes_file_when_no_other_references(
        self, delete_file, mock_delete_from_openai, assistant, resource, client
    ):
        """Test that file is completely deleted when not used in other resources."""
        team = assistant.team
        client.force_login(team.members.first())
        file = FileFactory(team=team, external_id="file_123", external_source="openai")

        # Setup: Add file to resource
        resource.files.add(file)
        mock_delete_from_openai.return_value = True

        # Create request
        response = client.delete(
            reverse("assistants:remove_file", args=[team.slug, assistant.id, resource.id, file.id])
        )

        # Assertions
        assert response.status_code == 200
        assert not resource.files.filter(id=file.id).exists()  # File removed from resource
        assert not File.objects.filter(id=file.id).exists()  # File deleted from database

        # Check that OpenAI deletion was called
        mock_delete_from_openai.assert_called_once()
        delete_file.assert_not_called()

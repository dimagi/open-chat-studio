import pytest

from apps.files.models import File
from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
class TestLlmChatResponse:
    def test_add_two_responses_with_text_only(self):
        """Test adding two LlmChatResponse instances with only text."""
        response1 = LlmChatResponse(text="Hello ")
        response2 = LlmChatResponse(text="World!")

        result = response1 + response2

        assert result.text == "Hello World!"
        assert result.cited_files == set()
        assert result.generated_files == set()

    def test_add_two_responses_with_cited_files(self):
        """Test adding two LlmChatResponse instances with cited files."""
        file1: File = FileFactory(name="file1.txt")  # ty: ignore[invalid-assignment]
        file2: File = FileFactory(name="file2.txt")  # ty: ignore[invalid-assignment]

        response1 = LlmChatResponse(text="Hello ", cited_files={file1})
        response2 = LlmChatResponse(text="World!", cited_files={file2})

        result = response1 + response2

        assert result.text == "Hello World!"
        assert result.cited_files == {file1, file2}
        assert result.generated_files == set()

    def test_add_two_responses_with_generated_files(self):
        """Test adding two LlmChatResponse instances with generated files."""
        file1: File = FileFactory(name="generated1.txt")  # ty: ignore[invalid-assignment]
        file2: File = FileFactory(name="generated2.txt")  # ty: ignore[invalid-assignment]

        response1 = LlmChatResponse(text="Hello ", generated_files={file1})
        response2 = LlmChatResponse(text="World!", generated_files={file2})

        result = response1 + response2

        assert result.text == "Hello World!"
        assert result.cited_files == set()
        assert result.generated_files == {file1, file2}

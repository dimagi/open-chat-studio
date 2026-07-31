from unittest.mock import Mock, patch

import httpx
import openai
import pytest
from langchain_core.messages import HumanMessage

from apps.chat.exceptions import UserReportableError
from apps.service_providers.llm_service.image_types import (
    DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES,
    GEMINI_SUPPORTED_IMAGE_CONTENT_TYPES,
)
from apps.service_providers.llm_service.utils import (
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
    format_multimodal_input,
    invoke_with_image_error_translation,
    populate_reference_section_from_citations,
    remove_citations_from_text,
)
from apps.utils.factories.files import FileFactory


def test_detangle_file_ids():
    assert detangle_file_ids(["file-092e", "file-123Abcfile-123Abc", "file-123Abcfile-456Bca"]) == [
        "file-092e",
        "file-123Abc",
        "file-123Abc",
        "file-123Abc",
        "file-456Bca",
    ]


@pytest.mark.parametrize(
    ("input_text", "expected_file_ids"),
    [
        ("", []),
        ("No citations here", []),
        ("<CIT 123 />", ["123"]),
        ("A citation <CIT 123 />. Another one<CIT 456 />.", ["123", "456"]),
    ],
)
def test_extract_file_ids_from_ocs_citations(input_text, expected_file_ids):
    result = extract_file_ids_from_ocs_citations(input_text)
    assert result == expected_file_ids


@pytest.mark.parametrize(
    ("text", "file_setups", "expected_output"),
    [
        # No citations
        (
            "Plain text without citations",
            [],
            "Plain text without citations",
        ),
        # Single citation
        (
            "Here is a fact <CIT 123 />.",
            [{"id": 123, "name": "document.pdf"}],
            "Here is a fact [^1].\n\n[^1]: [document.pdf](http://example.com/download/123)",
        ),
        # Multiple citations with different files
        (
            "Fact one <CIT 123 />. Fact two <CIT 456 />.",
            [{"id": 123, "name": "doc1.pdf"}, {"id": 456, "name": "doc2.txt"}],
            "Fact one [^1]. Fact two [^2].\n\n[^1]: [doc1.pdf](http://example.com/download/123)\n[^2]: [doc2.txt](http://example.com/download/456)",
        ),
        # Multiple citations with same file (should reuse citation number)
        (
            "First fact <CIT 123 />. Second fact <CIT 123 />.",
            [{"id": 123, "name": "document.pdf"}],
            "First fact [^1]. Second fact [^1].\n\n[^1]: [document.pdf](http://example.com/download/123)",
        ),
        # Mixed citations with reused and new files
        (
            "Fact A <CIT 123 />. Fact B <CIT 456 />. Fact C <CIT 123 />.",
            [{"id": 123, "name": "doc1.pdf"}, {"id": 456, "name": "doc2.txt"}],
            "Fact A [^1]. Fact B [^2]. Fact C [^1].\n\n[^1]: [doc1.pdf](http://example.com/download/123)\n[^2]: [doc2.txt](http://example.com/download/456)",
        ),
        # Citation with hallucinated file ID (should be removed)
        (
            "Valid fact <CIT 123 />. Invalid fact <CIT 999 />.",
            [{"id": 123, "name": "document.pdf"}],
            "Valid fact [^1]. Invalid fact .\n\n[^1]: [document.pdf](http://example.com/download/123)",
        ),
        # Multiple citations with some hallucinated IDs
        (
            "A <CIT 123 />. B <CIT 999 />. C <CIT 456 />. D <CIT 888 />.",
            [{"id": 123, "name": "doc1.pdf"}, {"id": 456, "name": "doc2.txt"}],
            "A [^1]. B . C [^2]. D .\n\n[^1]: [doc1.pdf](http://example.com/download/123)\n[^2]: [doc2.txt](http://example.com/download/456)",
        ),
        # Empty text with files
        (
            "",
            [{"id": 123, "name": "document.pdf"}],
            "",
        ),
        # Citation with special characters in filename
        (
            "Info from <CIT 123 />.",
            [{"id": 123, "name": "my file (v2).pdf"}],
            "Info from [^1].\n\n[^1]: [my file (v2).pdf](http://example.com/download/123)",
        ),
    ],
)
def test_populate_reference_section_from_citations(text, file_setups, expected_output):
    # Create file objects based on setups
    cited_files = []
    for file_setup in file_setups:
        file = FileFactory.build(id=file_setup["id"], name=file_setup["name"])
        # Mock the get_citation_url method to return a predictable URL
        file.get_citation_url = Mock(return_value=f"http://example.com/download/{file_setup['id']}")
        cited_files.append(file)

    # Test the function
    result = populate_reference_section_from_citations(text, cited_files, Mock())
    assert result == expected_output


def test_populate_reference_section_with_custom_citation():
    text = "Here is a fact <CIT 123 />."
    file = FileFactory.build(
        id=123, name="file name", metadata={"citation_url": "http://custom_link", "citation_text": "custom text"}
    )
    result = populate_reference_section_from_citations(text, [file], Mock())
    assert result == "Here is a fact [^1].\n\n[^1]: [custom text](http://custom_link)"


@pytest.mark.parametrize(
    ("input_text", "expected_output"),
    [
        ("No citations here", "No citations here"),
        ("Here is a citation <CIT 123 />", "Here is a citation "),
        ("<CIT 123 /> Here is a citation", " Here is a citation"),
        ("Here is a <CIT 123 /> citation", "Here is a  citation"),
        ("Multiple <CIT 123 /> citations <CIT 456 />", "Multiple  citations "),
        ("", ""),
        ("Text with no space<CIT 123 />around.", "Text with no spacearound."),
    ],
)
def test_remove_citations_from_text(input_text, expected_output):
    assert remove_citations_from_text(input_text) == expected_output


class TestFormatMultimodalInput:
    def test_text_only_message(self):
        result = format_multimodal_input("Hello world", [])

        assert isinstance(result, HumanMessage)
        assert result.content == [{"type": "text", "text": "Hello world"}]

    def test_image_attachment(self):
        attachment = Mock()
        attachment.size = 1024  # 1KB
        attachment.content_type = "image/jpeg"
        attachment.download_link = "http://example.com/image.jpg"
        attachment.name = "image.jpg"

        result = format_multimodal_input("Check this image", [attachment])

        assert isinstance(result, HumanMessage)
        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Check this image"}
        assert result.content[1] == {
            "type": "image",
            "source_type": "url",
            "url": "http://example.com/image.jpg",
            "mime_type": "image/jpeg",
        }

    def test_docx_attachment_converted_to_text(self):
        attachment = Mock()
        attachment.size = 1024  # 1KB
        attachment.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        attachment.name = "document.docx"
        attachment.read_text.return_value = "# Document Title\n\nThis is the document content."

        result = format_multimodal_input("Review this document", [attachment])

        assert isinstance(result, HumanMessage)
        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Review this document"}
        expected_text = (
            '<document filename="document.docx">\n# Document Title\n\nThis is the document content.\n</document>'
        )
        assert result.content[1] == {"type": "text", "text": expected_text}
        attachment.read_text.assert_called_once()

    def test_xlsx_attachment_converted_to_text(self):
        attachment = Mock()
        attachment.size = 2048  # 2KB
        attachment.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        attachment.name = "spreadsheet.xlsx"
        attachment.read_text.return_value = "| Col A | Col B |\n|-------|-------|\n| 1 | 2 |"

        result = format_multimodal_input("Analyze this spreadsheet", [attachment])

        assert isinstance(result, HumanMessage)
        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Analyze this spreadsheet"}
        expected_text = (
            '<document filename="spreadsheet.xlsx">\n| Col A | Col B |\n|-------|-------|\n| 1 | 2 |\n</document>'
        )
        assert result.content[1] == {"type": "text", "text": expected_text}

    def test_pdf_attachment_sent_as_file(self):
        attachment = Mock()
        attachment.size = 1024
        attachment.content_type = "application/pdf"
        attachment.name = "document.pdf"
        attachment.read_base64.return_value = "base64encodedcontent"

        result = format_multimodal_input("Review this PDF", [attachment])

        assert isinstance(result, HumanMessage)
        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Review this PDF"}
        assert result.content[1] == {
            "type": "file",
            "source_type": "base64",
            "data": "base64encodedcontent",
            "mime_type": "application/pdf",
            "filename": "document.pdf",
        }
        attachment.read_base64.assert_called_once()

    def test_docx_conversion_failure_skips_attachment(self):
        attachment = Mock()
        attachment.size = 1024
        attachment.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        attachment.name = "broken.docx"
        attachment.read_text.side_effect = Exception("Failed to read file")

        result = format_multimodal_input("Check this doc", [attachment])

        # When conversion fails, the attachment should be skipped (only text message)
        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Check this doc"}
        assert "Error" in result.content[1]["text"]

    def test_mixed_attachments(self):
        image_attachment = Mock()
        image_attachment.size = 1024
        image_attachment.content_type = "image/png"
        image_attachment.download_link = "http://example.com/image.png"
        image_attachment.name = "image.png"

        docx_attachment = Mock()
        docx_attachment.size = 2048
        docx_attachment.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        docx_attachment.name = "document.docx"
        docx_attachment.read_text.return_value = "Document content"

        pdf_attachment = Mock()
        pdf_attachment.size = 3072
        pdf_attachment.content_type = "application/pdf"
        pdf_attachment.name = "report.pdf"
        pdf_attachment.read_base64.return_value = "pdfbase64"

        result = format_multimodal_input("Review all files", [image_attachment, docx_attachment, pdf_attachment])

        assert len(result.content) == 4
        assert result.content[0]["type"] == "text"
        assert result.content[1]["type"] == "image"  # PNG image
        assert result.content[2]["type"] == "text"  # DOCX converted to text
        assert "Document content" in result.content[2]["text"]
        assert result.content[3]["type"] == "file"  # PDF as file

    def test_format_multimodal_input_excludes_send_to_llm_false(self):
        included = Mock()
        included.size = 1024
        included.content_type = "image/jpeg"
        included.download_link = "http://example.com/image.jpg"
        included.name = "image.jpg"
        included.send_to_llm = True

        excluded = Mock()
        excluded.size = 1024
        excluded.content_type = "image/png"
        excluded.download_link = "http://example.com/other.png"
        excluded.name = "other.png"
        excluded.send_to_llm = False

        result = format_multimodal_input("Hello", [included, excluded])

        assert len(result.content) == 2
        assert result.content[0] == {"type": "text", "text": "Hello"}
        assert result.content[1]["url"] == "http://example.com/image.jpg"

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_file_size_exceeds_max(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 10

        attachment = Mock()
        attachment.size = 20 * 1024 * 1024  # 20MB
        attachment.name = "large_file.docx"

        with pytest.raises(ValueError, match="exceeds maximum size"):
            format_multimodal_input("Process this", [attachment])

    @pytest.mark.parametrize(
        "content_type",
        [
            pytest.param("image/bmp", id="bmp"),
            pytest.param("image/svg+xml", id="svg"),
            pytest.param("image/heic", id="heic"),
            pytest.param("image/tiff", id="tiff"),
        ],
    )
    def test_unsupported_image_type_raises_user_reportable_error(self, content_type):
        attachment = Mock()
        attachment.size = 1024
        attachment.content_type = content_type
        attachment.name = "holiday-photo"

        with pytest.raises(UserReportableError) as exc_info:
            format_multimodal_input("Look at this", [attachment])

        assert "`holiday-photo`" in str(exc_info.value)
        assert "GIF, JPEG, PNG, WEBP" in str(exc_info.value)

    @pytest.mark.parametrize(
        "content_type",
        [
            pytest.param("image/png", id="png"),
            pytest.param("image/jpeg", id="jpeg"),
            pytest.param("image/gif", id="gif"),
            pytest.param("image/webp", id="webp"),
        ],
    )
    def test_supported_image_types_pass_through(self, content_type):
        attachment = Mock()
        attachment.size = 1024
        attachment.content_type = content_type
        attachment.download_link = "http://example.com/image"
        attachment.name = "image"

        result = format_multimodal_input("Look at this", [attachment])

        assert result.content[1]["mime_type"] == content_type

    def test_provider_specific_allowlist_is_respected(self):
        heic = Mock()
        heic.size = 1024
        heic.content_type = "image/heic"
        heic.download_link = "http://example.com/photo"
        heic.name = "photo"

        result = format_multimodal_input(
            "From my iPhone", [heic], supported_image_content_types=GEMINI_SUPPORTED_IMAGE_CONTENT_TYPES
        )

        assert result.content[1]["mime_type"] == "image/heic"

    def test_provider_specific_allowlist_rejects_with_provider_types_in_message(self):
        gif = Mock()
        gif.size = 1024
        gif.content_type = "image/gif"
        gif.name = "animation"

        with pytest.raises(UserReportableError) as exc_info:
            format_multimodal_input(
                "Fun gif", [gif], supported_image_content_types=GEMINI_SUPPORTED_IMAGE_CONTENT_TYPES
            )

        assert "HEIC, HEIF, JPEG, PNG, WEBP" in str(exc_info.value)

    def test_second_attachment_unsupported_fails_whole_message(self):
        """An unsupported image fails the whole message; attachments are never silently dropped."""
        good = Mock()
        good.size = 1024
        good.content_type = "image/png"
        good.download_link = "http://example.com/good"
        good.name = "good"
        bad = Mock()
        bad.size = 1024
        bad.content_type = "image/bmp"
        bad.name = "bad"

        with pytest.raises(UserReportableError):
            format_multimodal_input("Two images", [good, bad])


def _bad_request_error(code):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(
        "You uploaded an unsupported image.", response=response, body={"message": "unsupported image", "code": code}
    )


class TestInvokeWithImageErrorTranslation:
    @pytest.mark.parametrize(
        "code",
        [
            pytest.param("invalid_image_format", id="invalid_image_format"),
            pytest.param("invalid_image", id="invalid_image"),
            pytest.param("image_parse_error", id="image_parse_error"),
        ],
    )
    def test_invalid_image_error_is_translated_for_the_user(self, code):
        agent = Mock()
        agent.invoke.side_effect = _bad_request_error(code)

        with pytest.raises(UserReportableError) as exc_info:
            invoke_with_image_error_translation(
                agent, {"messages": []}, supported_image_content_types=DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
            )

        assert "GIF, JPEG, PNG, WEBP" in str(exc_info.value)

    def test_other_bad_request_errors_are_reraised(self):
        agent = Mock()
        agent.invoke.side_effect = _bad_request_error("context_length_exceeded")

        with pytest.raises(openai.BadRequestError):
            invoke_with_image_error_translation(
                agent, {"messages": []}, supported_image_content_types=DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
            )

    def test_successful_invoke_returns_result(self):
        agent = Mock()
        agent.invoke.return_value = {"messages": ["ok"]}

        result = invoke_with_image_error_translation(
            agent, {"messages": []}, supported_image_content_types=DEFAULT_SUPPORTED_IMAGE_CONTENT_TYPES
        )

        assert result == {"messages": ["ok"]}

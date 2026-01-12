from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import HumanMessage

from apps.service_providers.llm_service.utils import (
    MARKITDOWN_CONVERTIBLE_MIME_TYPES,
    _convert_attachment_to_text,
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
    format_multimodal_input,
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


class TestMarkitdownConvertibleMimeTypes:
    def test_docx_mime_type_in_convertible_types(self):
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert docx_mime in MARKITDOWN_CONVERTIBLE_MIME_TYPES

    def test_xlsx_mime_type_in_convertible_types(self):
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert xlsx_mime in MARKITDOWN_CONVERTIBLE_MIME_TYPES

    def test_pdf_not_in_convertible_types(self):
        # PDF is natively supported by LLM APIs, should not be in convertible types
        assert "application/pdf" not in MARKITDOWN_CONVERTIBLE_MIME_TYPES


class TestConvertAttachmentToText:
    def test_successful_conversion(self):
        attachment = Mock()
        attachment.name = "document.docx"
        attachment.read_text.return_value = "Converted text content"

        result = _convert_attachment_to_text(attachment)

        assert result == "Converted text content"
        attachment.read_text.assert_called_once()

    def test_conversion_failure_returns_none(self):
        attachment = Mock()
        attachment.name = "document.docx"
        attachment.read_text.side_effect = Exception("Conversion failed")

        result = _convert_attachment_to_text(attachment)

        assert result is None


class TestFormatMultimodalInput:
    @patch("apps.service_providers.llm_service.utils.settings")
    def test_text_only_message(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

        result = format_multimodal_input("Hello world", [])

        assert isinstance(result, HumanMessage)
        assert result.content == [{"type": "text", "text": "Hello world"}]

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_image_attachment(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

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

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_docx_attachment_converted_to_text(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

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
            '<document filename="document.docx">\n'
            "# Document Title\n\nThis is the document content.\n"
            "</document>"
        )
        assert result.content[1] == {"type": "text", "text": expected_text}
        attachment.read_text.assert_called_once()

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_xlsx_attachment_converted_to_text(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

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
            '<document filename="spreadsheet.xlsx">\n'
            "| Col A | Col B |\n|-------|-------|\n| 1 | 2 |\n"
            "</document>"
        )
        assert result.content[1] == {"type": "text", "text": expected_text}

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_pdf_attachment_sent_as_file(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

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

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_docx_conversion_failure_skips_attachment(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

        attachment = Mock()
        attachment.size = 1024
        attachment.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        attachment.name = "broken.docx"
        attachment.read_text.side_effect = Exception("Failed to read file")

        result = format_multimodal_input("Check this doc", [attachment])

        # When conversion fails, the attachment should be skipped (only text message)
        assert len(result.content) == 1
        assert result.content[0] == {"type": "text", "text": "Check this doc"}

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_mixed_attachments(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 50

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

        result = format_multimodal_input(
            "Review all files", [image_attachment, docx_attachment, pdf_attachment]
        )

        assert len(result.content) == 4
        assert result.content[0]["type"] == "text"
        assert result.content[1]["type"] == "image"  # PNG image
        assert result.content[2]["type"] == "text"  # DOCX converted to text
        assert "Document content" in result.content[2]["text"]
        assert result.content[3]["type"] == "file"  # PDF as file

    @patch("apps.service_providers.llm_service.utils.settings")
    def test_file_size_exceeds_max(self, mock_settings):
        mock_settings.MAX_FILE_SIZE_MB = 10

        attachment = Mock()
        attachment.size = 20 * 1024 * 1024  # 20MB
        attachment.name = "large_file.docx"

        with pytest.raises(ValueError, match="exceeds maximum size"):
            format_multimodal_input("Process this", [attachment])

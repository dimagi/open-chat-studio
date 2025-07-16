from unittest.mock import Mock

import pytest

from apps.service_providers.llm_service.utils import (
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
    populate_reference_section_from_citations,
    remove_citations_from_text,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
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


@pytest.mark.django_db()
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
    # Create experiment session
    session = ExperimentSessionFactory()

    # Create file objects based on setups
    cited_files = []
    for file_setup in file_setups:
        file = FileFactory(id=file_setup["id"], name=file_setup["name"], team=session.team)
        # Mock the download_link method to return a predictable URL
        file.download_link = Mock(return_value=f"http://example.com/download/{file_setup['id']}")
        cited_files.append(file)

    # Test the function
    result = populate_reference_section_from_citations(text, cited_files, session)
    assert result == expected_output


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

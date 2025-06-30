import pytest

from apps.service_providers.llm_service.utils import (
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
    remove_citation_tags,
)


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
        ("<CIT file_id=123 />", ["123"]),
        ("A citation <CIT file_id=123 />. Another one<CIT file_id=456 />.", ["123", "456"]),
    ],
)
def test_extract_file_ids_from_ocs_citations(input_text, expected_file_ids):
    result = extract_file_ids_from_ocs_citations(input_text)
    assert result == expected_file_ids


@pytest.mark.parametrize(
    ("input_text", "expected_output"),
    [
        ("", ""),
        ("No citations here", "No citations here"),
        ("<CIT 123 />", ""),
        ("Text with citation<CIT 123 />.", "Text with citation."),
        ('<xml something="123" />\n.\t<CIT 123 />', '<xml something="123" />\n.\t'),
        ("\n<a href=''></a>", "\n<a href=''></a>"),
        ("Text with multiple<CIT 123 /> citations<CIT 456 />.", "Text with multiple citations."),
        ("Citation with extra space <CIT 123  />.", "Citation with extra space ."),
        ("<div><p>Some text</p><CIT 123 /></div>", "<div><p>Some text</p></div>"),
        ("<CIT 123 /><CIT 456 />", ""),
    ],
)
def test_remove_ocs_citations(input_text, expected_output):
    result = remove_citation_tags(input_text)
    assert result == expected_output

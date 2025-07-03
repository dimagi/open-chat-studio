import pytest

from apps.service_providers.llm_service.utils import (
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
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
        ("<CIT 123 />", ["123"]),
        ("A citation <CIT 123 />. Another one<CIT 456 />.", ["123", "456"]),
    ],
)
def test_extract_file_ids_from_ocs_citations(input_text, expected_file_ids):
    result = extract_file_ids_from_ocs_citations(input_text)
    assert result == expected_file_ids

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from apps.documents.datamodels import GoogleDocsSourceConfig
from apps.documents.source_loaders.google_docs import (
    DOCS_API_URL,
    GoogleDocsDocumentLoader,
    _google_api_error_message,
    extract_document_id,
    extract_document_text,
    normalize_document_text,
    safe_document_filename,
)
from apps.service_providers.auth_service.oauth import OAuthTokenError
from apps.service_providers.models import AuthProviderType


def test_extract_document_id_accepts_supported_urls():
    for suffix in ["edit", "view", ""]:
        assert extract_document_id(f"https://docs.google.com/document/d/doc_123/{suffix}") == "doc_123"


@pytest.mark.parametrize(
    "value",
    [
        "http://docs.google.com/document/d/doc_123/edit",
        "https://docs.google.com/spreadsheets/d/doc_123/edit",
        "https://example.com/document/d/doc_123/edit",
        "https://docs.google.com/drive/folders/doc_123",
        "https://docs.google.com/document/d/bad.id/edit",
        "doc_123",
    ],
)
def test_extract_document_id_rejects_unsafe_values(value):
    with pytest.raises(ValueError, match=r".+"):
        extract_document_id(value)


def test_extract_document_text_handles_tabs_nested_tabs_tables_and_lists():
    document = {
        "tabs": [
            {
                "tabProperties": {"title": "Overview"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                                    "elements": [{"textRun": {"content": "Title\n"}}],
                                }
                            },
                            {"paragraph": {"bullet": {}, "elements": [{"textRun": {"content": "Item\n"}}]}},
                            {
                                "table": {
                                    "tableRows": [
                                        {
                                            "tableCells": [
                                                {
                                                    "content": [
                                                        {"paragraph": {"elements": [{"textRun": {"content": "A\n"}}]}}
                                                    ]
                                                },
                                                {
                                                    "content": [
                                                        {"paragraph": {"elements": [{"textRun": {"content": "B\n"}}]}}
                                                    ]
                                                },
                                            ]
                                        }
                                    ]
                                }
                            },
                        ]
                    }
                },
                "childTabs": [
                    {
                        "tabProperties": {"title": "Details"},
                        "documentTab": {
                            "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Nested\n"}}]}}]}
                        },
                    }
                ],
            },
            {
                "tabProperties": {"title": "Second"},
                "documentTab": {
                    "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Other\n"}}]}}]}
                },
            },
        ]
    }
    text = extract_document_text(document)
    assert "## Overview" in text
    assert "# Title" in text
    assert "- Item" in text
    assert "A | B" in text
    assert "## Details" in text
    assert "Nested" in text
    assert "## Second" in text


def _source(provider):
    return SimpleNamespace(
        team_id=1,
        auth_provider=provider,
        config=SimpleNamespace(
            google_docs=GoogleDocsSourceConfig(document_url="https://docs.google.com/document/d/doc_123/edit")
        ),
    )


def test_loader_fetches_tabs_with_bearer_and_content_hash(httpx_mock):
    auth_service = Mock()
    auth_service.get_auth_headers.return_value = {"Authorization": "Bearer token"}
    provider = SimpleNamespace(
        team_id=1, type=AuthProviderType.oauth_authorization_code, get_auth_service=Mock(return_value=auth_service)
    )
    httpx_mock.add_response(
        url=f"{DOCS_API_URL}/doc_123?includeTabsContent=true",
        json={
            "title": "Test",
            "tabs": [
                {
                    "documentTab": {
                        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]}
                    }
                }
            ],
        },
    )
    loader = GoogleDocsDocumentLoader.for_document_source(SimpleNamespace(id=7, team_id=1), _source(provider))
    document = next(loader.load_documents())
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer token"
    assert request.url.params["includeTabsContent"] == "true"
    assert document.metadata["document_id"] == "doc_123"
    assert document.metadata["title"] == "Test"
    assert document.metadata["content_hash"]
    assert document.content == b"Hello\n"


def test_loader_rejects_missing_or_wrong_team_or_type_provider():
    collection = SimpleNamespace(id=7, team_id=1)
    for provider in [
        None,
        SimpleNamespace(team_id=2, type=AuthProviderType.oauth_authorization_code),
        SimpleNamespace(team_id=1, type=AuthProviderType.bearer),
    ]:
        with pytest.raises(ValueError, match=r".+"):
            GoogleDocsDocumentLoader.for_document_source(collection, _source(provider))


def test_loader_surfaces_http_errors_without_empty_success(httpx_mock):
    auth_service = Mock()
    auth_service.get_auth_headers.return_value = {}
    provider = SimpleNamespace(
        team_id=1, type=AuthProviderType.oauth_authorization_code, get_auth_service=Mock(return_value=auth_service)
    )
    httpx_mock.add_response(url=f"{DOCS_API_URL}/doc_123?includeTabsContent=true", status_code=503)
    loader = GoogleDocsDocumentLoader(SimpleNamespace(id=7), _source(provider).config.google_docs, provider)
    with pytest.raises(ValueError, match="Google Docs request failed"):
        list(loader.load_documents())


@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "The caller does not have permission",
                    "errors": [{"reason": "forbidden"}],
                }
            },
            "403, PERMISSION_DENIED, reason=forbidden",
        ),
        (
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "API has not been used",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                }
            },
            "403, PERMISSION_DENIED, reason=SERVICE_DISABLED",
        ),
        (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}, "429, RESOURCE_EXHAUSTED"),
        (404, {"error": {"status": "NOT_FOUND", "message": "Document not found"}}, "404, NOT_FOUND"),
    ],
)
def test_google_api_error_contains_safe_structured_details(status_code, payload, expected):
    response = httpx.Response(status_code, json=payload)
    message = _google_api_error_message(response)
    assert expected in message
    assert "Authorization" not in message
    assert "token" not in message.lower()


def test_google_api_error_handles_non_json_response():
    response = httpx.Response(500, text="upstream failure")
    assert _google_api_error_message(response) == "Google Docs request failed (500): HTTP error"


def test_loader_does_not_wrap_oauth_errors_as_invalid_response():
    provider = SimpleNamespace(
        team_id=1,
        type=AuthProviderType.oauth_authorization_code,
        get_auth_service=Mock(side_effect=OAuthTokenError("invalid_grant", error_code="invalid_grant")),
    )
    loader = GoogleDocsDocumentLoader(
        SimpleNamespace(id=7),
        GoogleDocsSourceConfig(document_url="https://docs.google.com/document/d/doc_123"),
        provider,
    )
    with pytest.raises(OAuthTokenError, match="invalid_grant"):
        list(loader.load_documents())


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Untitled Meeting - Notes", "Untitled Meeting - Notes.txt"),
        ("bad:/\\name*?", "bad name.txt"),
        ("...", "doc_123.txt"),
    ],
)
def test_safe_document_filename(title, expected):
    assert safe_document_filename(title, "doc_123") == expected


def test_loader_metadata_preserves_content_and_stable_identifier(httpx_mock):
    auth_service = Mock()
    auth_service.get_auth_headers.return_value = {"Authorization": "Bearer token"}
    provider = SimpleNamespace(
        team_id=1,
        type=AuthProviderType.oauth_authorization_code,
        get_auth_service=Mock(return_value=auth_service),
    )
    source = _source(provider)
    httpx_mock.add_response(
        url=f"{DOCS_API_URL}/doc_123?includeTabsContent=true",
        json={
            "title": "Untitled Meeting - Notes",
            "tabs": [
                {
                    "documentTab": {
                        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Body\n"}}]}}]}
                    }
                }
            ],
        },
    )
    loader = GoogleDocsDocumentLoader.for_document_source(SimpleNamespace(id=7, team_id=1), source)
    document = next(loader.load_documents())
    assert document.metadata["filename"] == "Untitled Meeting - Notes.txt"
    assert loader.get_document_identifier(document) == "doc_123"
    assert document.content == b"Body\n"


def test_normalize_document_text_removes_control_artifacts_preserves_structure():
    assert normalize_document_text("Heading\x0bparagraph\n\n- item\x00\nA | B") == "Heading paragraph\n\n- item \nA | B"


def test_extracted_text_keeps_paragraph_separation_and_content_semantics():
    document = {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Summary\x0b\n"}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "Details\n"}}]}},
            ]
        }
    }
    assert extract_document_text(document) == "Summary \nDetails\n"


def test_title_change_updates_filename_without_changing_identifier_or_content():
    loader = GoogleDocsDocumentLoader(
        SimpleNamespace(id=7),
        GoogleDocsSourceConfig(document_url="https://docs.google.com/document/d/doc_123"),
        SimpleNamespace(),
    )
    content = b"Unchanged body\n"
    metadata = {"content_hash": hashlib.sha256(content).hexdigest(), "filename": "New title.txt"}
    existing_file = SimpleNamespace(file=SimpleNamespace(name="Old title.txt", metadata=metadata))
    document = SimpleNamespace(content=content, metadata=metadata)
    assert loader.get_document_identifier(SimpleNamespace(metadata={"document_id": "doc_123"})) == "doc_123"
    assert loader.should_update_document(document, existing_file)
    existing_file.file.name = "New title.txt"
    assert not loader.should_update_document(document, existing_file)

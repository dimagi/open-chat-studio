from unittest import mock
from unittest.mock import Mock

import httpx
import pytest

from apps.documents.datamodels import (
    DocumentSourceConfig,
    JSONCollectionSourceConfig,
)
from apps.documents.models import SourceType
from apps.documents.readers import Document as ReaderDocument
from apps.documents.readers import DocumentPart
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.documents.source_loaders.registry import LOADERS


@pytest.fixture()
def json_config():
    return JSONCollectionSourceConfig(json_url="https://example.com/feed.json")


def _make_loader(json_config, collection_id=42):
    collection = Mock()
    collection.id = collection_id
    return JSONCollectionLoader(collection=collection, config=json_config, auth_provider=None)


def _stub_doc(text: str) -> ReaderDocument:
    return ReaderDocument(parts=[DocumentPart(content=text)])


class TestJSONCollectionLoaderConstruction:
    def test_for_document_source_constructs_loader_without_auth(self, json_config):
        collection = Mock()
        document_source = Mock()
        document_source.config = DocumentSourceConfig(json_collection=json_config)
        document_source.auth_provider = None

        loader = JSONCollectionLoader.for_document_source(collection, document_source)

        assert isinstance(loader, JSONCollectionLoader)
        assert loader.collection is collection
        assert loader.config is json_config
        assert loader.auth_provider is None

    def test_loader_registered(self):
        assert LOADERS[SourceType.JSON_COLLECTION] is JSONCollectionLoader


class TestLoadDocumentsRoot:
    def test_http_error_raises(self, json_config, httpx_mock):
        httpx_mock.add_response(url="https://example.com/feed.json", status_code=500, json={})
        loader = _make_loader(json_config)
        with pytest.raises(httpx.HTTPStatusError):
            list(loader.load_documents())

    def test_non_list_root_raises(self, json_config, httpx_mock):
        httpx_mock.add_response(url="https://example.com/feed.json", json={"results": []})
        loader = _make_loader(json_config)
        with pytest.raises(ValueError, match="expected a JSON list"):
            list(loader.load_documents())

    def test_empty_list_yields_nothing(self, json_config, httpx_mock):
        httpx_mock.add_response(url="https://example.com/feed.json", json=[])
        loader = _make_loader(json_config)
        assert list(loader.load_documents()) == []


class TestLoadDocumentsAttachments:
    def test_multi_attachment_yields_one_doc_per_attachment(self, json_config, httpx_mock):
        feed = [
            {
                "title": "Doc A",
                "URI": "https://example.com/a",
                "date": "08/04/2025",
                "type": "Meeting reports",
                "languages": ["en"],
                "attachments": [
                    {
                        "file_type": "pdf",
                        "file_size": "100KB",
                        "title": "First PDF",
                        "link": "https://example.com/a-1.pdf",
                    },
                    {
                        "file_type": "pdf",
                        "file_size": "200KB",
                        "title": "Second PDF",
                        "link": "https://example.com/a-2.pdf",
                    },
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(
            url="https://example.com/a-1.pdf",
            content=b"%PDF-content-1",
            headers={"content-type": "application/pdf"},
        )
        httpx_mock.add_response(
            url="https://example.com/a-2.pdf",
            content=b"%PDF-content-2",
            headers={"content-type": "application/pdf"},
        )

        loader = _make_loader(json_config, collection_id=42)
        with mock.patch("apps.documents.source_loaders.json_collection.markitdown_read") as md:
            md.side_effect = [
                _stub_doc("text from pdf 1"),
                _stub_doc("text from pdf 2"),
            ]
            docs = list(loader.load_documents())

        assert len(docs) == 2
        assert docs[0].page_content == "text from pdf 1"
        assert docs[0].metadata["source"] == "https://example.com/a-1.pdf"
        assert docs[0].metadata["link"] == "https://example.com/a-1.pdf"
        assert docs[0].metadata["file_type"] == "pdf"
        assert docs[0].metadata["attachment_title"] == "First PDF"
        # item-level metadata is propagated:
        assert docs[0].metadata["title"] == "Doc A"
        assert docs[0].metadata["URI"] == "https://example.com/a"
        assert docs[0].metadata["date"] == "08/04/2025"
        assert docs[0].metadata["languages"] == ["en"]
        # framework-required:
        assert docs[0].metadata["collection_id"] == 42
        assert docs[0].metadata["source_type"] == "json_collection"
        assert docs[0].metadata["citation_text"] == "Doc A"
        assert docs[0].metadata["citation_url"] == "https://example.com/a"

        assert docs[1].page_content == "text from pdf 2"
        assert docs[1].metadata["source"] == "https://example.com/a-2.pdf"

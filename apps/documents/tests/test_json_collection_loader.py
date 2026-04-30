import logging
from unittest import mock
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.documents import Document as LCDocument

from apps.documents.datamodels import (
    DocumentSourceConfig,
    JSONCollectionSourceConfig,
)
from apps.documents.models import SourceType
from apps.documents.readers import Document as ReaderDocument
from apps.documents.readers import DocumentPart
from apps.documents.source_loaders import json_collection as jc
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.documents.source_loaders.registry import LOADERS
from apps.utils.urlvalidate import InvalidURL


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

    def test_for_document_source_forwards_auth_provider(self, json_config):
        collection = Mock()
        document_source = Mock()
        document_source.config = DocumentSourceConfig(json_collection=json_config)
        auth_provider = Mock()
        document_source.auth_provider = auth_provider

        loader = JSONCollectionLoader.for_document_source(collection, document_source)

        assert loader.auth_provider is auth_provider

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


class TestLoadDocumentsFallback:
    def test_no_attachments_field_yields_fallback(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "date": "01/01/2025",
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)

        loader = _make_loader(json_config)
        docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "T"
        assert docs[0].metadata["source"] == "https://example.com/page"
        assert docs[0].metadata["title"] == "T"
        assert docs[0].metadata["URI"] == "https://example.com/page"
        assert docs[0].metadata["citation_text"] == "T"
        assert docs[0].metadata["citation_url"] == "https://example.com/page"
        assert "link" not in docs[0].metadata

    def test_empty_attachments_yields_fallback(self, json_config, httpx_mock):
        feed = [{"title": "T", "URI": "https://example.com/page", "attachments": []}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert len(docs) == 1
        assert docs[0].page_content == "T"

    def test_attachments_without_links_yields_fallback(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [{"file_type": "pdf", "file_size": "?"}],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert len(docs) == 1
        assert docs[0].page_content == "T"

    def test_optional_metadata_fields_propagate_when_present(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/p",
                "authors": "Alice",
                "publisher": "Pub",
                "countries": ["US", "CA"],
                "diseases": ["malaria"],
                "tags": ["health"],
                "regions": ["AFRO"],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        meta = docs[0].metadata
        for k, v in [
            ("authors", "Alice"),
            ("publisher", "Pub"),
            ("countries", ["US", "CA"]),
            ("diseases", ["malaria"]),
            ("tags", ["health"]),
            ("regions", ["AFRO"]),
        ]:
            assert meta[k] == v

    def test_optional_metadata_fields_absent_when_missing(self, json_config, httpx_mock):
        feed = [{"title": "T", "URI": "https://example.com/p"}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        meta = list(loader.load_documents())[0].metadata
        for k in ("authors", "publisher", "countries", "diseases", "tags", "regions"):
            assert k not in meta

    def test_item_missing_title_and_uri_is_skipped(self, json_config, httpx_mock, caplog):
        feed = [{"date": "01/01/2025"}, {"title": "OK", "URI": "https://example.com/p"}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)

        with caplog.at_level(logging.WARNING, logger="apps.documents.source_loaders.json_collection"):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].metadata["title"] == "OK"
        assert any("neither 'title' nor 'URI'" in record.message for record in caplog.records)


class TestLoadDocumentsAttachmentFailures:
    def test_one_attachment_404_others_yielded(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "bad", "link": "https://example.com/bad.pdf"},
                    {"file_type": "pdf", "title": "good", "link": "https://example.com/good.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/bad.pdf", status_code=404, content=b"")
        httpx_mock.add_response(url="https://example.com/good.pdf", content=b"PDF")

        loader = _make_loader(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read",
            return_value=_stub_doc("good text"),
        ):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "good text"
        assert docs[0].metadata["link"] == "https://example.com/good.pdf"

    def test_extraction_error_skips_attachment(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "broken", "link": "https://example.com/x.pdf"},
                    {"file_type": "pdf", "title": "ok", "link": "https://example.com/y.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/x.pdf", content=b"junk")
        httpx_mock.add_response(url="https://example.com/y.pdf", content=b"ok")

        loader = _make_loader(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read",
            side_effect=[RuntimeError("boom"), _stub_doc("ok text")],
        ):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "ok text"
        assert docs[0].metadata["link"] == "https://example.com/y.pdf"

    def test_all_attachments_fail_yields_zero_documents_no_fallback(self, json_config, httpx_mock):
        """Transient failures must NOT produce a URI-keyed fallback that races
        against link-keyed Documents on subsequent syncs."""
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "1", "link": "https://example.com/1.pdf"},
                    {"file_type": "pdf", "title": "2", "link": "https://example.com/2.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/1.pdf", status_code=500, content=b"")
        httpx_mock.add_response(url="https://example.com/2.pdf", status_code=500, content=b"")

        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert docs == []


class TestGetDocumentIdentifier:
    def test_attachment_doc_identifier_is_link(self, json_config):
        loader = _make_loader(json_config)
        doc = LCDocument(
            page_content="x",
            metadata={"link": "https://example.com/file.pdf", "source": "https://example.com/file.pdf"},
        )
        assert loader.get_document_identifier(doc) == "https://example.com/file.pdf"

    def test_fallback_doc_identifier_is_source(self, json_config):
        loader = _make_loader(json_config)
        doc = LCDocument(page_content="t", metadata={"source": "https://example.com/page"})
        assert loader.get_document_identifier(doc) == "https://example.com/page"


class TestShouldUpdateDocument:
    def _make_existing_file(self, metadata):
        existing = Mock()
        existing.file = Mock()
        existing.file.metadata = metadata
        return existing

    def test_same_date_means_no_update(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={"date": "01/01/2025"})
        existing = self._make_existing_file({"date": "01/01/2025"})
        assert loader.should_update_document(new_doc, existing) is False

    def test_different_date_means_update(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={"date": "02/01/2025"})
        existing = self._make_existing_file({"date": "01/01/2025"})
        assert loader.should_update_document(new_doc, existing) is True

    def test_missing_date_defers_to_base(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={})
        existing = self._make_existing_file({})
        # base class returns True (always update)
        assert loader.should_update_document(new_doc, existing) is True


class TestSSRFAndSizeLimits:
    def test_attachment_with_private_ip_is_skipped(self, json_config, httpx_mock, caplog):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "ssrf", "link": "https://example.com/internal.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)

        def fake_validate(url, strict):
            if "internal.pdf" in url:
                raise InvalidURL("Unsafe IP address")

        loader = _make_loader(json_config)
        with (
            mock.patch(
                "apps.documents.source_loaders.json_collection.validate_user_input_url",
                side_effect=fake_validate,
            ),
            caplog.at_level(logging.WARNING, logger="apps.documents.source_loaders.json_collection"),
        ):
            docs = list(loader.load_documents())
        assert docs == []
        assert any("internal.pdf" in record.message for record in caplog.records)

    def test_json_url_with_private_ip_aborts_sync(self, json_config):
        loader = _make_loader(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.validate_user_input_url",
            side_effect=InvalidURL("Unsafe IP address"),
        ):
            with pytest.raises(ValueError, match="Refusing to fetch JSON URL"):
                list(loader.load_documents())

    def test_oversized_response_raises(self, json_config, httpx_mock):
        big_payload = b"x" * 1024  # 1 KB, but cap is going to be smaller
        httpx_mock.add_response(url="https://example.com/feed.json", content=big_payload)
        loader = _make_loader(json_config)
        with mock.patch.object(jc, "MAX_RESPONSE_BYTES", 100):
            with pytest.raises(ValueError, match="exceeds .* byte cap"):
                list(loader.load_documents())


def _loader_with_fake_auth(json_config, header_name="Authorization", header_value="Bearer secret"):
    auth_provider = Mock()
    auth_service = Mock()
    auth_service.get_auth_headers.return_value = {header_name: header_value}
    auth_provider.get_auth_service.return_value = auth_service
    collection = Mock()
    collection.id = 42
    return JSONCollectionLoader(collection=collection, config=json_config, auth_provider=auth_provider)


class TestAuthHeadersPropagation:
    def test_no_auth_provider_means_no_auth_headers(self, json_config):
        loader = _make_loader(json_config)
        assert loader._get_auth_headers() == {}

    def test_auth_headers_pulled_from_auth_provider(self, json_config):
        loader = _loader_with_fake_auth(json_config, "Authorization", "Bearer secret")
        assert loader._get_auth_headers() == {"Authorization": "Bearer secret"}

    def test_auth_headers_applied_to_json_fetch(self, json_config, httpx_mock):
        httpx_mock.add_response(
            url="https://example.com/feed.json",
            json=[],
            match_headers={"Authorization": "Bearer secret"},
        )
        loader = _loader_with_fake_auth(json_config)
        # If the headers don't match, httpx_mock will raise; reaching this assertion means they did.
        assert list(loader.load_documents()) == []

    def test_auth_headers_applied_to_attachment_fetch(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "f", "link": "https://example.com/file.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(
            url="https://example.com/feed.json",
            json=feed,
            match_headers={"Authorization": "Bearer secret"},
        )
        httpx_mock.add_response(
            url="https://example.com/file.pdf",
            content=b"PDF",
            match_headers={"Authorization": "Bearer secret"},
        )
        loader = _loader_with_fake_auth(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read",
            return_value=_stub_doc("body"),
        ):
            docs = list(loader.load_documents())
        assert len(docs) == 1
        assert docs[0].page_content == "body"

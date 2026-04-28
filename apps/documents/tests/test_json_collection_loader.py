from unittest.mock import Mock

import httpx
import pytest

from apps.documents.datamodels import (
    DocumentSourceConfig,
    JSONCollectionSourceConfig,
)
from apps.documents.models import SourceType
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.documents.source_loaders.registry import LOADERS


@pytest.fixture()
def json_config():
    return JSONCollectionSourceConfig(json_url="https://example.com/feed.json")


def _make_loader(json_config, collection_id=42):
    collection = Mock()
    collection.id = collection_id
    return JSONCollectionLoader(collection=collection, config=json_config, auth_provider=None)


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

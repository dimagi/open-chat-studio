from unittest.mock import Mock

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

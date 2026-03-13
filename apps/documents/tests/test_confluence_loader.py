from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from langchain_core.documents import Document

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.source_loaders.confluence import ConfluenceDocumentLoader


@pytest.fixture()
def confluence_config():
    return ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", space_key="demo")


class TestConfluenceDocumentLoader:
    @pytest.mark.parametrize(
        ("config_kwargs", "expected_field", "expected_value"),
        [
            ({"space_key": "DEMO"}, "space_key", "DEMO"),
            ({"label": "important"}, "label", "important"),
            ({"cql": "space = DEMO"}, "cql", "space = DEMO"),
            ({"page_ids": "123,456,789"}, "page_ids", "123,456,789"),
        ],
    )
    def test_validate_config_valid(self, config_kwargs, expected_field, expected_value):
        config = ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", **config_kwargs)
        assert getattr(config, expected_field) == expected_value
        assert config.base_url == "https://site.atlassian.net/wiki"

    def test_validate_config_no_loading_option(self):
        with pytest.raises(ValueError, match="At least one loading option must be specified"):
            ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki")

    def test_validate_config_multiple_loading_options(self):
        with pytest.raises(ValueError, match="Only one loading option can be specified"):
            ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", space_key="DEMO", label="important")

    @pytest.mark.parametrize(
        ("config_kwargs", "expected_loader_kwargs"),
        [
            ({"space_key": "DEMO"}, {"url": "https://site.atlassian.net/wiki", "max_pages": 1000, "space_key": "DEMO"}),
            (
                {"label": "important"},
                {"url": "https://site.atlassian.net/wiki", "max_pages": 1000, "label": "important"},
            ),
            (
                {"cql": "space = DEMO AND type = page"},
                {"url": "https://site.atlassian.net/wiki", "max_pages": 1000, "cql": "space = DEMO AND type = page"},
            ),
            (
                {"page_ids": "123,456, 789"},
                {"url": "https://site.atlassian.net/wiki", "max_pages": 1000, "page_ids": [123, 456, 789]},
            ),
        ],
    )
    def test_get_loader_kwargs(self, config_kwargs, expected_loader_kwargs):
        config = ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", **config_kwargs)
        kwargs = config.get_loader_kwargs()
        assert kwargs == expected_loader_kwargs

    def test_get_loader_kwargs_custom_max_pages(self):
        config = ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", space_key="DEMO", max_pages=500)
        kwargs = config.get_loader_kwargs()
        expected = {"url": "https://site.atlassian.net/wiki", "max_pages": 500, "space_key": "DEMO"}
        assert kwargs == expected

    def test_get_loader_kwargs_invalid_page_ids(self):
        config = ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", page_ids="123, abc, 789")
        with pytest.raises(ValueError, match="Page IDs must be comma-separated integers"):
            config.get_loader_kwargs()

    def test_load_documents(self, confluence_config):
        loader = ConfluenceDocumentLoader(
            Mock(), confluence_config, Mock(config={"username": "jack", "password": "password"})
        )
        with patch("langchain_community.document_loaders.confluence.ConfluenceLoader.lazy_load") as lazy_load:
            lazy_load.return_value = _get_mock_document_iterator()
            documents = list(loader.load_documents())

        expected_raw_docs = _get_mock_documents()
        assert [doc.page_content for doc in documents] == [doc.page_content for doc in expected_raw_docs]
        assert all("source_type" in doc.metadata for doc in documents)


def _get_mock_document_iterator() -> Iterator[Document]:
    yield from _get_mock_documents()


def _get_mock_documents(paths: list[str] | None = None) -> list[Document]:
    paths = paths or ["page 1", "page 2"]
    metadata = [
        {
            "title": path,
            "id": index,
            "source": f"source for {path}",
        }
        for index, path in enumerate(paths)
    ]
    return [Document(page_content=f"test {i}", metadata=meta) for i, meta in enumerate(metadata)]

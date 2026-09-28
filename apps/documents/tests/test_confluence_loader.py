from unittest.mock import Mock, call, patch

import pytest
from tenacity import wait_none

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.source_loaders import confluence as confluence_module
from apps.documents.source_loaders.confluence import ConfluenceDocumentLoader

BASE_URL = "https://site.atlassian.net/wiki"


def _page(page_id, html="<p>Hello <b>world</b></p>", status="current", when="2026-01-01T00:00:00Z"):
    return {
        "id": str(page_id),
        "title": f"Page {page_id}",
        "status": status,
        "body": {"storage": {"value": html}},
        "version": {"when": when},
        "_links": {"webui": f"/spaces/DEMO/pages/{page_id}"},
    }


def _unrestricted():
    return {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}}


@pytest.fixture()
def client():
    client = Mock()
    client.get_all_restrictions_for_content.return_value = _unrestricted()
    with patch.object(confluence_module, "Confluence", return_value=client) as confluence_class:
        client.confluence_class = confluence_class
        yield client


@pytest.fixture(autouse=True)
def no_retry_wait():
    with patch.object(confluence_module, "RETRY_WAIT", wait_none()):
        yield


def _load(**config_kwargs):
    config = ConfluenceSourceConfig(base_url=BASE_URL, **config_kwargs)
    loader = ConfluenceDocumentLoader(Mock(id=7), config, Mock(config={"username": "jack", "password": "secret"}))
    return list(loader.load_documents())


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


class TestLoadDocuments:
    def test_space_documents_and_metadata(self, client):
        client.get_all_pages_from_space.side_effect = [[_page(1)], []]
        [document] = _load(space_key="DEMO")

        client.confluence_class.assert_called_once_with(url=BASE_URL, username="jack", password="secret", cloud=True)
        client.get_all_pages_from_space.assert_any_call(
            space="DEMO", start=0, limit=50, status="current", expand="body.storage,version"
        )
        assert document.content == b"Hello world"
        assert document.metadata == {
            "title": "Page 1",
            "id": "1",
            "source": f"{BASE_URL}/spaces/DEMO/pages/1",
            "when": "2026-01-01T00:00:00Z",
            "collection_id": 7,
            "source_type": "confluence",
            "base_url": BASE_URL,
            "citation_text": "Page 1",
            "citation_url": f"{BASE_URL}/spaces/DEMO/pages/1",
        }

    def test_space_pagination_stops_at_max_pages(self, client):
        client.get_all_pages_from_space.side_effect = [
            [_page(i) for i in range(50)],
            [_page(i) for i in range(50, 100)],
        ]
        documents = _load(space_key="DEMO", max_pages=60)
        assert len(documents) == 60
        assert [c.kwargs["start"] for c in client.get_all_pages_from_space.call_args_list] == [0, 50]

    def test_label_fetches_each_page_by_id(self, client):
        client.get_all_pages_by_label.side_effect = [[{"id": "1"}, {"id": "2"}, {"id": "1"}], []]
        client.get_page_by_id.side_effect = lambda page_id, expand: _page(page_id)
        documents = _load(label="important")
        assert [d.metadata["id"] for d in documents] == ["1", "2"]
        client.get_page_by_id.assert_has_calls(
            [call(page_id="1", expand="body.storage,version"), call(page_id="2", expand="body.storage,version")]
        )

    def test_cql_follows_next_links(self, client):
        client.get.side_effect = [
            {"results": [_page(1)], "_links": {"next": "/rest/api/content/search?cursor=abc"}},
            {"results": [_page(2)], "_links": {}},
        ]
        documents = _load(cql="space = DEMO")
        assert [d.metadata["id"] for d in documents] == ["1", "2"]
        assert client.get.call_args_list == [
            call(
                "rest/api/content/search",
                params={
                    "cql": "space = DEMO",
                    "limit": 50,
                    "expand": "body.storage,version",
                    "includeArchivedSpaces": False,
                },
            ),
            call("/rest/api/content/search?cursor=abc"),
        ]

    def test_page_ids(self, client):
        client.get_page_by_id.side_effect = lambda page_id, expand: _page(page_id)
        documents = _load(page_ids="11, 12")
        assert [d.metadata["id"] for d in documents] == ["11", "12"]

    @pytest.mark.parametrize(
        "config_kwargs",
        [
            pytest.param({"space_key": "DEMO"}, id="space"),
            pytest.param({"label": "important"}, id="label"),
            pytest.param({"cql": "space = DEMO"}, id="cql"),
            pytest.param({"page_ids": "1,2,3"}, id="page-ids"),
        ],
    )
    def test_restricted_and_archived_pages_are_skipped(self, client, config_kwargs):
        pages = {"1": _page(1), "2": _page(2, status="archived"), "3": _page(3)}
        client.get_all_pages_from_space.side_effect = [list(pages.values()), []]
        client.get_all_pages_by_label.side_effect = [[{"id": i} for i in pages], []]
        client.get.return_value = {"results": list(pages.values()), "_links": {}}
        client.get_page_by_id.side_effect = lambda page_id, expand: pages[str(page_id)]
        restricted = {"read": {"restrictions": {"user": {"results": [{"id": "u"}]}, "group": {"results": []}}}}
        client.get_all_restrictions_for_content.side_effect = lambda page_id: (
            restricted if page_id == "3" else _unrestricted()
        )

        assert [d.metadata["id"] for d in _load(**config_kwargs)] == ["1"]

    def test_blank_pages_are_skipped(self, client):
        client.get_all_pages_from_space.side_effect = [[_page(1, html="<p>  </p>"), _page(2)], []]
        assert [d.metadata["id"] for d in _load(space_key="DEMO")] == ["2"]

    def test_transient_errors_are_retried(self, client):
        client.get_all_pages_from_space.side_effect = [ConnectionError("boom"), [_page(1)], []]
        assert len(_load(space_key="DEMO")) == 1

    def test_errors_propagate_after_retries(self, client):
        client.get_all_pages_from_space.side_effect = ConnectionError("boom")
        with pytest.raises(ConnectionError):
            _load(space_key="DEMO")
        assert client.get_all_pages_from_space.call_count == 3

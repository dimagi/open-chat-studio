from unittest.mock import Mock, call, patch

import pydantic
import pytest
from tenacity import wait_none

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.source_loaders import confluence as confluence_module
from apps.documents.source_loaders.confluence import ConfluenceDocumentLoader

BASE_URL = "https://site.atlassian.net/wiki"


def _page(page_id, html="<p>Hello <b>world</b></p>", status="current", when="2026-01-01T00:00:00Z", ancestors=()):
    return {
        "id": str(page_id),
        "title": f"Page {page_id}",
        "status": status,
        "body": {"storage": {"value": html}},
        "version": {"when": when},
        "ancestors": [{"id": str(ancestor_id)} for ancestor_id in ancestors],
        "_links": {"webui": f"/spaces/DEMO/pages/{page_id}"},
    }


def _unrestricted():
    return {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}}


def _restricted():
    return {"read": {"restrictions": {"user": {"results": [{"id": "u"}]}, "group": {"results": []}}}}


def _restricted_ids(*content_ids):
    """A `get_all_restrictions_for_content` stand-in that restricts reading `content_ids`."""
    return lambda content_id: _restricted() if content_id in content_ids else _unrestricted()


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


def _load_lazily(**config_kwargs):
    config = ConfluenceSourceConfig(base_url=BASE_URL, **config_kwargs)
    loader = ConfluenceDocumentLoader(Mock(id=7), config, Mock(config={"username": "jack", "password": "secret"}))
    return loader.load_documents()


def _load(**config_kwargs):
    return list(_load_lazily(**config_kwargs))


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

    @pytest.mark.parametrize("max_pages", [pytest.param(0, id="zero"), pytest.param(-1, id="negative")])
    def test_max_pages_must_be_positive(self, max_pages):
        with pytest.raises(pydantic.ValidationError, match="max_pages"):
            ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", space_key="DEMO", max_pages=max_pages)

    def test_get_loader_kwargs_invalid_page_ids(self):
        config = ConfluenceSourceConfig(base_url="https://site.atlassian.net/wiki", page_ids="123, abc, 789")
        with pytest.raises(ValueError, match="Page IDs must be comma-separated integers"):
            config.get_loader_kwargs()


class TestLoadDocuments:
    def test_space_documents_and_metadata(self, client):
        client.get_all_pages_from_space_raw.side_effect = [{"results": [_page(1)]}, {"results": []}]
        [document] = _load(space_key="DEMO")

        client.confluence_class.assert_called_once_with(url=BASE_URL, username="jack", password="secret", cloud=True)
        client.get_all_pages_from_space_raw.assert_any_call(
            space="DEMO", start=0, limit=50, status="current", expand="body.storage,version,ancestors"
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
        client.get_all_pages_from_space_raw.side_effect = [
            {"results": [_page(i) for i in range(50)]},
            {"results": [_page(i) for i in range(50, 100)]},
        ]
        documents = _load(space_key="DEMO", max_pages=60)
        assert len(documents) == 60
        assert [c.kwargs["start"] for c in client.get_all_pages_from_space_raw.call_args_list] == [0, 50]

    def test_label_fetches_each_page_by_id(self, client):
        client.get_all_pages_by_label.side_effect = [[{"id": "1"}, {"id": "2"}, {"id": "1"}], []]
        client.get_page_by_id.side_effect = lambda page_id, expand: _page(page_id)
        documents = _load(label="important")
        assert [d.metadata["id"] for d in documents] == ["1", "2"]
        client.get_page_by_id.assert_has_calls(
            [
                call(page_id="1", expand="body.storage,version,ancestors"),
                call(page_id="2", expand="body.storage,version,ancestors"),
            ]
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
                    "expand": "body.storage,version,ancestors",
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
        client.get_all_pages_from_space_raw.side_effect = [{"results": list(pages.values())}, {"results": []}]
        client.get_all_pages_by_label.side_effect = [[{"id": i} for i in pages], []]
        client.get.return_value = {"results": list(pages.values()), "_links": {}}
        client.get_page_by_id.side_effect = lambda page_id, expand: pages[str(page_id)]
        client.get_all_restrictions_for_content.side_effect = _restricted_ids("3")

        assert [d.metadata["id"] for d in _load(**config_kwargs)] == ["1"]

    def test_pages_under_a_restricted_ancestor_are_skipped(self, client):
        pages = [_page(1, ancestors=[10]), _page(2, ancestors=[20, 21]), _page(3, ancestors=[30])]
        client.get_all_pages_from_space_raw.side_effect = [{"results": pages}, {"results": []}]
        client.get_all_restrictions_for_content.side_effect = _restricted_ids("21", "30")

        assert [d.metadata["id"] for d in _load(space_key="DEMO")] == ["1"]

    def test_each_ancestor_restriction_is_looked_up_once(self, client):
        pages = [_page(1, ancestors=[10]), _page(2, ancestors=[10, 1])]
        client.get_all_pages_from_space_raw.side_effect = [{"results": pages}, {"results": []}]

        assert [d.metadata["id"] for d in _load(space_key="DEMO")] == ["1", "2"]
        looked_up = [c.args[0] for c in client.get_all_restrictions_for_content.call_args_list]
        assert sorted(looked_up) == ["1", "10", "2"]

    def test_ancestors_are_fetched_when_the_page_has_none_listed(self, client):
        page = _page(1)
        del page["ancestors"]
        client.get_all_pages_from_space_raw.side_effect = [{"results": [page]}, {"results": []}]
        client.get_page_ancestors.return_value = [{"id": "10"}]
        client.get_all_restrictions_for_content.side_effect = _restricted_ids("10")

        assert _load(space_key="DEMO") == []
        client.get_page_ancestors.assert_called_once_with("1")

    @pytest.mark.parametrize(
        ("config_kwargs", "fetch_name", "batches"),
        [
            pytest.param(
                {"space_key": "DEMO"},
                "get_all_pages_from_space_raw",
                [{"results": [_page(1)]}, {"results": [_page(2)]}, {"results": []}],
                id="space",
            ),
            pytest.param(
                {"label": "important"},
                "get_all_pages_by_label",
                [[{"id": "1"}], [{"id": "2"}], []],
                id="label",
            ),
            pytest.param(
                {"cql": "space = DEMO"},
                "get",
                [{"results": [_page(1)], "_links": {"next": "/next"}}, {"results": [_page(2)], "_links": {}}],
                id="cql",
            ),
        ],
    )
    def test_pages_are_loaded_one_batch_at_a_time(self, client, config_kwargs, fetch_name, batches):
        fetch = getattr(client, fetch_name)
        fetch.side_effect = batches
        client.get_page_by_id.side_effect = lambda page_id, expand: _page(page_id)
        documents = _load_lazily(**config_kwargs)

        assert next(documents).metadata["id"] == "1"
        assert fetch.call_count == 1
        assert [d.metadata["id"] for d in documents] == ["2"]

    def test_blank_pages_are_skipped(self, client):
        client.get_all_pages_from_space_raw.side_effect = [
            {"results": [_page(1, html="<p>  </p>"), _page(2)]},
            {"results": []},
        ]
        assert [d.metadata["id"] for d in _load(space_key="DEMO")] == ["2"]

    def test_transient_errors_are_retried(self, client):
        client.get_all_pages_from_space_raw.side_effect = [
            ConnectionError("boom"),
            {"results": [_page(1)]},
            {"results": []},
        ]
        assert len(_load(space_key="DEMO")) == 1

    def test_transient_restriction_errors_are_retried(self, client):
        client.get_all_pages_from_space_raw.side_effect = [{"results": [_page(1)]}, {"results": []}]
        client.get_all_restrictions_for_content.side_effect = [ConnectionError("boom"), _unrestricted()]
        assert len(_load(space_key="DEMO")) == 1

    def test_errors_propagate_after_retries(self, client):
        client.get_all_pages_from_space_raw.side_effect = ConnectionError("boom")
        with pytest.raises(ConnectionError):
            _load(space_key="DEMO")
        assert client.get_all_pages_from_space_raw.call_count == 3

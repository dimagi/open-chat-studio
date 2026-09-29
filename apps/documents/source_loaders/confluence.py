import logging
from collections.abc import Callable, Iterator
from typing import Self

from atlassian import Confluence
from bs4 import BeautifulSoup
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)

PAGE_EXPAND = "body.storage,version"
BATCH_SIZE = 50

RETRY_WAIT = wait_exponential(multiplier=1, min=2, max=10)


def _with_retries[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    return retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=RETRY_WAIT,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )(fn)


class ConfluenceDocumentLoader(BaseDocumentLoader[ConfluenceSourceConfig]):
    """Document loader for Confluence spaces"""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        auth_provider = document_source.auth_provider
        if not auth_provider or auth_provider.type != AuthProviderType.basic:
            type_ = auth_provider.type if auth_provider else "None"
            raise ValueError(f"Confluence document source requires a basic authentication provider, got {type_}")
        if not auth_provider.config.get("username") or not auth_provider.config.get("password"):
            raise ValueError("Confluence authentication both username and password")
        return cls(collection, document_source.config.confluence, auth_provider)

    def load_documents(self) -> Iterator[SourceDocument]:
        """Load documents from Confluence using configured options"""
        try:
            username = self.auth_provider.config.get("username")
            if not username:
                raise ValueError("Confluence authentication requires username or email")

            client = Confluence(
                url=self.config.base_url,
                username=username,
                password=self.auth_provider.config.get("password"),
                cloud=True,
            )
            for page in self._fetch_pages(client, self.config.get_loader_kwargs()):
                if not _is_public(client, page):
                    continue
                # Confluence serves pages as HTML, so unlike the other loaders these are not the
                # source's own bytes. There is no rawer representation to hand on: the API has no file to serve.
                text = BeautifulSoup(page["body"]["storage"]["value"], "lxml").get_text(" ", strip=True)
                if not text:
                    continue
                yield SourceDocument(content=text.encode("utf-8"), metadata=self._page_metadata(page))
        except Exception as e:
            logger.error(f"Error loading documents from Confluence: {e!s}")
            raise

    def _fetch_pages(self, client: Confluence, options: dict) -> Iterator[dict]:
        max_pages = options["max_pages"]
        if space_key := options.get("space_key"):
            yield from _paginate(
                lambda start: client.get_all_pages_from_space_raw(
                    space=space_key, start=start, limit=BATCH_SIZE, status="current", expand=PAGE_EXPAND
                )["results"],
                max_pages,
            )
        elif label := options.get("label"):
            labelled = _paginate(
                lambda start: client.get_all_pages_by_label(label=label, start=start, limit=BATCH_SIZE), max_pages
            )
            for page_id in dict.fromkeys(page["id"] for page in labelled):
                yield _with_retries(client.get_page_by_id)(page_id=page_id, expand=PAGE_EXPAND)
        elif cql := options.get("cql"):
            yield from _search_cql(client, cql, max_pages)
        elif page_ids := options.get("page_ids"):
            for page_id in page_ids:
                yield _with_retries(client.get_page_by_id)(page_id=page_id, expand=PAGE_EXPAND)

    def _page_metadata(self, page: dict) -> dict:
        source = self.config.base_url.strip("/") + page["_links"]["webui"]
        metadata = {"title": page["title"], "id": page["id"], "source": source}
        if when := page.get("version", {}).get("when"):
            metadata["when"] = when
        metadata.update(
            {
                "collection_id": self.collection.id,
                "source_type": "confluence",
                "base_url": self.config.base_url,
                "citation_text": page["title"],
                "citation_url": source,
            }
        )
        return metadata

    def get_document_identifier(self, document: SourceDocument) -> str:
        """Get a unique identifier for a Confluence document"""
        page_id = document.metadata.get("id")
        if page_id:
            return f"confluence://{self.config.base_url}/{page_id}"
        return document.metadata.get("source", "")

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
        # Check if last modified time changed
        new_modified = document.metadata.get("when")
        old_modified = existing_file.file.metadata.get("when")

        if new_modified and old_modified:
            return new_modified != old_modified
        return super().should_update_document(document, existing_file)


def _paginate(fetch_batch: Callable[[int], list[dict]], max_pages: int) -> list[dict]:
    pages: list[dict] = []
    while len(pages) < max_pages:
        batch = _with_retries(fetch_batch)(len(pages))
        if not batch:
            break
        pages.extend(batch)
    return pages[:max_pages]


def _search_cql(client: Confluence, cql: str, max_pages: int) -> list[dict]:
    pages: list[dict] = []
    params = {"cql": cql, "limit": BATCH_SIZE, "expand": PAGE_EXPAND, "includeArchivedSpaces": False}
    response = _with_retries(client.get)("rest/api/content/search", params=params)
    while True:
        pages.extend(response.get("results", []))
        next_url = response.get("_links", {}).get("next")
        if not next_url or len(pages) >= max_pages:
            return pages[:max_pages]
        response = _with_retries(client.get)(next_url)


def _is_public(client: Confluence, page: dict) -> bool:
    if page["status"] != "current":
        return False
    read = client.get_all_restrictions_for_content(page["id"])["read"]["restrictions"]
    return not read["user"]["results"] and not read["group"]["results"]

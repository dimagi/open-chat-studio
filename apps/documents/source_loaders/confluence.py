import logging
from collections.abc import Callable, Iterator
from typing import Self

from atlassian import Confluence
from atlassian.errors import ApiError
from bs4 import BeautifulSoup
from requests import HTTPError
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)

PAGE_EXPAND = "body.storage,version,ancestors"
BATCH_SIZE = 50

RETRY_WAIT = wait_exponential(multiplier=1, min=2, max=10)
SKIPPED_STATUS_CODES = (403, 404)


def _with_retries[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    return retry(
        reraise=True,
        retry=retry_if_exception(lambda e: not _is_forbidden_or_missing(e)),
        stop=stop_after_attempt(3),
        wait=RETRY_WAIT,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )(fn)


def _is_forbidden_or_missing(error: BaseException) -> bool:
    """Whether `error` is Confluence answering 403 or 404, which retrying will not change."""
    cause = error.reason if isinstance(error, ApiError) else error
    return isinstance(cause, HTTPError) and getattr(cause.response, "status_code", None) in SKIPPED_STATUS_CODES


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
            client = Confluence(
                url=self.config.base_url,
                username=self.auth_provider.config.get("username"),
                password=self.auth_provider.config.get("password"),
                cloud=True,
            )
            restricted: dict[str, bool] = {}
            for page in self._fetch_pages(client, self.config.get_loader_kwargs()):
                if not _for_page(page["id"], _is_public, client, page, restricted):
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
            seen = set()
            for page in labelled:
                if page["id"] not in seen:
                    seen.add(page["id"])
                    if fetched := _get_page(client, page["id"]):
                        yield fetched
        elif cql := options.get("cql"):
            yield from _search_cql(client, cql, max_pages)
        elif page_ids := options.get("page_ids"):
            for page_id in page_ids:
                if page := _get_page(client, page_id):
                    yield page

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


def _for_page[**P, R](page_id, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R | None:
    """`fn(*args, **kwargs)`, or None with a warning when Confluence answers 403 or 404 for page `page_id`."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if not _is_forbidden_or_missing(e):
            raise
        logger.warning("Skipping Confluence page %s: %s", page_id, e)
        return None


def _get_page(client: Confluence, page_id) -> dict | None:
    return _for_page(page_id, _with_retries(client.get_page_by_id), page_id=page_id, expand=PAGE_EXPAND)


def _paginate(fetch_batch: Callable[[int], list[dict]], max_pages: int) -> Iterator[dict]:
    fetched = 0
    while fetched < max_pages:
        batch = _with_retries(fetch_batch)(fetched)
        if not batch:
            return
        yield from batch[: max_pages - fetched]
        fetched += len(batch)


def _search_cql(client: Confluence, cql: str, max_pages: int) -> Iterator[dict]:
    params = {"cql": cql, "limit": BATCH_SIZE, "expand": PAGE_EXPAND, "includeArchivedSpaces": False}
    response = _with_retries(client.get)("rest/api/content/search", params=params)
    fetched = 0
    while True:
        results = response.get("results", [])
        yield from results[: max_pages - fetched]
        fetched += len(results)
        next_url = response.get("_links", {}).get("next")
        if not next_url or fetched >= max_pages:
            return
        response = _with_retries(client.get)(next_url)


def _is_public(client: Confluence, page: dict, restricted: dict[str, bool]) -> bool:
    """Whether `page` is current and has no read restriction of its own or inherited from an ancestor page."""
    if page["status"] != "current":
        return False
    ancestors = page["ancestors"] if "ancestors" in page else _with_retries(client.get_page_ancestors)(page["id"])
    content_ids = [page["id"], *(ancestor["id"] for ancestor in ancestors)]
    return not any(_has_read_restrictions(client, content_id, restricted) for content_id in content_ids)


def _has_read_restrictions(client: Confluence, content_id: str, restricted: dict[str, bool]) -> bool:
    if content_id not in restricted:
        try:
            read = _with_retries(client.get_all_restrictions_for_content)(content_id)["read"]["restrictions"]
        except Exception as e:
            if not _is_forbidden_or_missing(e):
                raise
            logger.warning("Treating Confluence content %s as restricted: %s", content_id, e)
            restricted[content_id] = True
        else:
            restricted[content_id] = bool(read["user"]["results"] or read["group"]["results"])
    return restricted[content_id]

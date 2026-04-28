import json
import logging
from collections.abc import Iterator
from io import BytesIO
from typing import Any, Self

import httpx
from django.conf import settings
from langchain_core.documents import Document

from apps.documents.datamodels import JSONCollectionSourceConfig
from apps.documents.models import Collection, DocumentSource
from apps.documents.readers import markitdown_read
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.utils.urlvalidate import InvalidURL, validate_user_input_url

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB cap on JSON feed and per-attachment download


class JSONCollectionLoader(BaseDocumentLoader[JSONCollectionSourceConfig]):
    """Document loader for JSON 'indexed collections' feeds."""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        return cls(collection, document_source.config.json_collection, auth_provider=None)

    def load_documents(self) -> Iterator[Document]:
        """Load documents from a JSON indexed-collections feed."""
        items = self._fetch_json_list()
        for item in items:
            yield from self._process_item(item)

    def _fetch_json_list(self) -> list[dict[str, Any]]:
        try:
            validate_user_input_url(str(self.config.json_url), strict=not settings.DEBUG)
        except InvalidURL as exc:
            raise ValueError(f"Refusing to fetch JSON URL: {exc}") from exc
        content = self._read_with_size_limit(str(self.config.json_url))
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list at the top level, got {type(data).__name__}")
        return data

    def _process_item(self, item: dict[str, Any]) -> Iterator[Document]:
        title = item.get("title")
        uri = item.get("URI")
        if not title and not uri:
            logger.warning(
                "Skipping item with neither 'title' nor 'URI': %r",
                item,
            )
            return

        item_metadata = self._build_item_metadata(item, title=title, uri=uri)
        fetchable = [a for a in (item.get("attachments") or []) if a.get("link")]

        if fetchable:
            yield from self._yield_attachment_documents(item_metadata, fetchable, uri)
            return

        # Fallback: no fetchable attachments
        if title:
            yield Document(
                page_content=title,
                metadata={**item_metadata, "source": uri or ""},
            )
        else:
            logger.warning(
                "Skipping item with no fetchable attachments and no title (URI=%s)",
                uri,
            )

    def _build_item_metadata(self, item: dict[str, Any], *, title: str | None, uri: str | None) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "collection_id": self.collection.id,
            "source_type": "json_collection",
        }
        if title is not None:
            metadata["title"] = title
            metadata["citation_text"] = title
        if uri is not None:
            metadata["URI"] = uri
            metadata["citation_url"] = uri
        for key in (
            "date",
            "type",
            "languages",
            "authors",
            "publisher",
            "countries",
            "diseases",
            "tags",
            "regions",
        ):
            if key in item:
                metadata[key] = item[key]
        return metadata

    def _yield_attachment_documents(
        self,
        item_metadata: dict[str, Any],
        fetchable: list[dict[str, Any]],
        item_uri: str | None,
    ) -> Iterator[Document]:
        for attachment in fetchable:
            link = attachment["link"]
            try:
                text = self._fetch_and_extract(link)
            except Exception as exc:  # noqa: BLE001 -- caught and logged per design
                logger.warning(
                    "Skipping attachment %s for item %s: %s",
                    link,
                    item_uri,
                    exc,
                )
                continue
            attachment_metadata: dict[str, Any] = {"link": link, "source": link}
            for src_key, dst_key in (
                ("file_type", "file_type"),
                ("file_size", "file_size"),
                ("title", "attachment_title"),
            ):
                if src_key in attachment:
                    attachment_metadata[dst_key] = attachment[src_key]
            metadata = {**item_metadata, **attachment_metadata}
            yield Document(page_content=text, metadata=metadata)

    def _fetch_and_extract(self, url: str) -> str:
        try:
            validate_user_input_url(url, strict=not settings.DEBUG)
        except InvalidURL as exc:
            raise ValueError(f"Refusing to fetch attachment URL: {exc}") from exc
        content = self._read_with_size_limit(url)
        doc = markitdown_read(BytesIO(content))
        return doc.get_contents_as_string()

    def _read_with_size_limit(self, url: str) -> bytes:
        """GET `url` and return the body, raising ValueError if it exceeds the size cap."""
        with httpx.stream(
            "GET",
            url,
            timeout=self.config.request_timeout,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError(f"Response from {url} exceeds {MAX_RESPONSE_BYTES} byte cap")
                chunks.append(chunk)
            return b"".join(chunks)

    def get_document_identifier(self, document: Document) -> str:
        link = document.metadata.get("link")
        if link:
            return link
        return super().get_document_identifier(document)

    def should_update_document(self, document: Document, existing_file) -> bool:
        new_date = document.metadata.get("date")
        old_date = existing_file.file.metadata.get("date") if existing_file.file else None
        if new_date and old_date:
            return new_date != old_date
        return super().should_update_document(document, existing_file)

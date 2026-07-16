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
        return cls(collection, document_source.config.json_collection, auth_provider=document_source.auth_provider)

    def _get_auth_headers(self) -> dict[str, str]:
        if self.auth_provider is None:
            return {}
        return self.auth_provider.get_auth_service().get_auth_headers()

    def load_documents(self) -> Iterator[Document]:
        """Load documents from a JSON indexed-collections feed."""
        items = self._fetch_json_list()
        total = len(items)
        skipped = 0
        loaded = 0
        for item in items:
            before = loaded
            for doc in self._process_item(item):
                loaded += 1
                yield doc
            if loaded == before:
                skipped += 1
        logger.info(
            "JSON collection sync complete: %d item(s) in feed, %d document(s) loaded, %d item(s) produced no documents",
            total,
            loaded,
            skipped,
        )

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

        # All filtering happens before any network I/O.
        if not self._passes_metadata_filters(item, label=uri or title):
            return

        fetchable = self._fetchable_attachments(item)
        if not fetchable:
            logger.warning(
                "Skipping item %s: no attachments with a document link",
                uri or title,
            )
            return

        item_metadata = self._build_item_metadata(item, title=title, uri=uri)
        yield from self._yield_attachment_documents(item_metadata, fetchable, uri)

    def _passes_metadata_filters(self, item: dict[str, Any], *, label: str | None) -> bool:
        """Evaluate the configured metadata filters against the raw item."""
        unmatched = next((f for f in self.config.metadata_filters if not f.matches(item)), None)
        if unmatched is not None:
            logger.debug("Skipping item %s: did not satisfy metadata filter on %r", label, unmatched.field)
            return False
        return True

    @staticmethod
    def _fetchable_attachments(item: dict[str, Any]) -> list[dict[str, Any]]:
        return [a for a in (item.get("attachments") or []) if a.get("link")]

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
            "topics",
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
            file_type = attachment.get("file_type")
            if self.config.is_unsupported_file_type(file_type):
                logger.warning(
                    "Skipping attachment %s for item %s: unsupported file type %r",
                    link,
                    item_uri,
                    file_type,
                )
                continue
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
        """GET `url` and return the body, raising ValueError if it exceeds the size cap.

        Auth headers from the configured AuthProvider (if any) are applied to every request.
        """
        with httpx.stream(
            "GET",
            url,
            timeout=self.config.request_timeout,
            follow_redirects=True,
            headers=self._get_auth_headers(),
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

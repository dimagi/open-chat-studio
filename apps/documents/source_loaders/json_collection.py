import logging
from collections.abc import Iterator
from typing import Any, Self

import httpx
from langchain_core.documents import Document

from apps.documents.datamodels import JSONCollectionSourceConfig
from apps.documents.models import Collection, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader

logger = logging.getLogger(__name__)


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
        response = httpx.get(
            str(self.config.json_url),
            timeout=self.config.request_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list at the top level, got {type(data).__name__}")
        return data

    def _process_item(self, item: dict[str, Any]) -> Iterator[Document]:
        return iter(())  # filled in by Task 6+

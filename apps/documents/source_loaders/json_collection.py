import logging
from collections.abc import Iterator
from typing import Self

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
        raise NotImplementedError  # implemented in later tasks
        yield  # pragma: no cover  -- keeps function an Iterator

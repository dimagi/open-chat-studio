from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Generic, Self, TypeVar

from langchain_core.documents import Document

from apps.documents.models import Collection, CollectionFile


@dataclass
class SyncResult:
    """Result of a document source sync operation"""

    success: bool
    files_added: int = 0
    files_updated: int = 0
    files_removed: int = 0
    error_message: str = ""
    duration_seconds: float = 0.0

    @property
    def total_files_processed(self) -> int:
        return self.files_added + self.files_updated + self.files_removed


ConfigType = TypeVar("ConfigType")


class BaseDocumentLoader(ABC, Generic[ConfigType]):
    """Abstract base class for document loaders"""

    def __init__(self, collection: Collection, config: ConfigType, auth_provider: Any = None):
        self.collection = collection
        self.config = config
        self.auth_provider = auth_provider

    @classmethod
    @abstractmethod
    def for_document_source(cls, collection, document_source) -> Self:
        pass

    @abstractmethod
    def load_documents(self) -> Iterator[Document]:
        """
        Load documents from the external source.

        Document metadata:
            * collection_id: (required) The ID of the collection.
            * source_type: (required) The type of the loader.
            * citation_text: (optional) Custom citation text.
            * citation_url: (optional) Custom URL to use when citing the document as a source.

        Returns:
            List of LangChain Document objects
        """
        pass

    def get_document_identifier(self, document: Document) -> str:
        """
        Get a unique identifier for a document to track changes.
        By default, use the source metadata 'source' field.

        Args:
            document: LangChain Document object

        Returns:
            Unique identifier string
        """
        return document.metadata.get("source", "")

    def should_update_document(self, document: Document, existing_file: CollectionFile) -> bool:
        """
        Determine if a document should be updated based on metadata comparison.

        Args:
            document: New document from source
            existing_file: Existing CollectionFile object

        Returns:
            True if document should be updated
        """
        return True

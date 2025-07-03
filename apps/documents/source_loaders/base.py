from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document

from apps.documents.models import Collection


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


class BaseDocumentLoader(ABC):
    """Abstract base class for document loaders"""

    def __init__(self, config: dict, collection: Collection):
        self.config = config
        self.collection = collection

    @abstractmethod
    def load_documents(self) -> list[Document]:
        """
        Load documents from the external source.

        Returns:
            List of LangChain Document objects
        """
        pass

    @abstractmethod
    def get_source_metadata(self) -> dict[str, Any]:
        """
        Get metadata about the source (e.g., last modified time, version info).

        Returns:
            Dictionary containing source metadata
        """
        pass

    @abstractmethod
    def validate_config(self) -> tuple[bool, str]:
        """
        Validate the loader configuration.

        Returns:
            Tuple of (is_valid, error_message)
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

    def should_update_document(self, document: Document, existing_metadata: dict) -> bool:
        """
        Determine if a document should be updated based on metadata comparison.

        Args:
            document: New document from source
            existing_metadata: Metadata from previously synced version

        Returns:
            True if document should be updated
        """
        # Default implementation: compare last modified times if available
        new_modified = document.metadata.get("last_modified")
        old_modified = existing_metadata.get("last_modified")

        if new_modified and old_modified:
            return new_modified != old_modified

        # If no modification time, assume update needed
        return True

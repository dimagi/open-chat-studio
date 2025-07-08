import logging
import time

from django.db import transaction
from django.utils import timezone
from langchain_core.documents import Document

from apps.documents.models import (
    ChunkingStrategy,
    CollectionFile,
    CollectionFileMetadata,
    DocumentSource,
    DocumentSourceSyncLog,
    FileStatus,
    SyncStatus,
)
from apps.documents.source_loaders.base import SyncResult
from apps.documents.source_loaders.registry import create_loader
from apps.files.models import File

logger = logging.getLogger(__name__)


class DocumentSourceManager:
    """Service for managing document source synchronization"""

    def __init__(self, document_source: DocumentSource):
        self.document_source = document_source
        self.collection = document_source.collection

    def sync_collection(self) -> SyncResult:
        """
        Synchronize the collection with its document source.

        Returns:
            SyncResult with sync statistics and status
        """
        start_time = time.time()
        sync_log = None

        try:
            # Create sync log
            sync_log = DocumentSourceSyncLog.objects.create(
                document_source=self.document_source, status=SyncStatus.IN_PROGRESS
            )

            # Validate configuration
            loader = create_loader(
                self.document_source.source_type, self.document_source.source_config.model_dump(), self.collection
            )

            is_valid, error_msg = loader.validate_config()
            if not is_valid:
                raise ValueError(f"Invalid configuration: {error_msg}")

            # Load documents from source
            logger.info(f"Loading documents from {self.document_source.source_type} source")
            documents = loader.load_documents()

            # Sync documents with collection
            result = self._sync_documents(documents, loader)

            # Update sync log
            duration = time.time() - start_time
            sync_log.status = SyncStatus.SUCCESS
            sync_log.files_added = result.files_added
            sync_log.files_updated = result.files_updated
            sync_log.files_removed = result.files_removed
            sync_log.duration_seconds = duration
            sync_log.save()

            # Update document source last sync time
            self.document_source.last_sync = timezone.now()
            self.document_source.save(update_fields=["last_sync"])

            result.duration_seconds = duration
            result.success = True

            logger.info(
                f"Sync completed: {result.files_added} added, "
                f"{result.files_updated} updated, {result.files_removed} removed"
            )

            return result

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            logger.error(f"Sync failed: {error_msg}")

            if sync_log:
                sync_log.status = SyncStatus.FAILED
                sync_log.error_message = error_msg
                sync_log.duration_seconds = duration
                sync_log.save()

            return SyncResult(success=False, error_message=error_msg, duration_seconds=duration)

    def _sync_documents(self, documents: list[Document], loader) -> SyncResult:
        """
        Sync documents with the collection, handling additions, updates, and removals.

        Args:
            documents: List of documents from the source
            loader: Document loader instance

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(success=True)

        with transaction.atomic():
            # Get existing files for this collection that are from document sources
            existing_files = File.objects.filter(
                collections=self.collection, external_source__startswith=self.document_source.source_type
            )

            # Create mapping of document identifiers to existing files
            existing_files_map = {}
            for file in existing_files:
                # Extract identifier from external_source
                if file.external_source:
                    existing_files_map[file.external_source] = file

            # Track which files we've seen in this sync
            seen_identifiers = set()

            # Process each document
            for document in documents:
                identifier = loader.get_document_identifier(document)
                seen_identifiers.add(identifier)

                if identifier in existing_files_map:
                    # File exists, check if it needs updating
                    existing_file = existing_files_map[identifier]
                    if loader.should_update_document(document, existing_file.metadata or {}):
                        self._update_file(existing_file, document, identifier)
                        result.files_updated += 1
                else:
                    # New file, create it
                    self._create_file(document, identifier)
                    result.files_added += 1

            # Remove files that are no longer in the source
            files_to_remove = [
                file for identifier, file in existing_files_map.items() if identifier not in seen_identifiers
            ]

            for file in files_to_remove:
                self._remove_file(file)
                result.files_removed += 1

        return result

    def _create_file(self, document: Document, identifier: str):
        """Create a new file from a document"""
        # Extract filename from document metadata or identifier
        filename = self._extract_filename(document, identifier)

        # Create File object
        file = File.objects.create(
            team=self.collection.team,
            name=filename,
            content=document.page_content.encode("utf-8"),
            content_type="text/plain",
            external_id="",  # Not using external storage
            external_source=identifier,
            metadata=document.metadata,
        )

        # Create CollectionFile relationship
        collection_file = CollectionFile.objects.create(
            collection=self.collection,
            file=file,
            status=FileStatus.PENDING,
            metadata=CollectionFileMetadata(chunking_strategy=ChunkingStrategy(chunk_size=800, chunk_overlap=400)),
        )

        # If collection is indexed, trigger indexing
        if self.collection.is_index:
            self._index_file(collection_file)

    def _update_file(self, existing_file: File, document: Document, identifier: str):
        """Update an existing file with new document content"""
        existing_file.content = document.page_content.encode("utf-8")
        existing_file.metadata = document.metadata
        existing_file.save(update_fields=["content", "metadata"])

        # If collection is indexed, re-index the file
        if self.collection.is_index:
            collection_file = CollectionFile.objects.get(collection=self.collection, file=existing_file)
            collection_file.status = FileStatus.PENDING
            collection_file.save(update_fields=["status"])
            self._index_file(collection_file)

    def _remove_file(self, file: File):
        """Remove a file that's no longer in the source"""
        # Remove from collection
        CollectionFile.objects.filter(collection=self.collection, file=file).delete()

        # If file is not used by other collections, archive it
        if not file.collections.exclude(id=self.collection.id).exists():
            file.is_archived = True
            file.save(update_fields=["is_archived"])

    def _extract_filename(self, document: Document, identifier: str) -> str:
        """Extract a suitable filename from document metadata or identifier"""
        # Try to get filename from metadata
        if "source" in document.metadata:
            source = document.metadata["source"]
            if "/" in source:
                return source.split("/")[-1]
            return source

        # Fall back to using part of the identifier
        if ":" in identifier:
            parts = identifier.split(":")
            if len(parts) > 2:
                return parts[-1] or "document.txt"

        return "document.txt"

    def _index_file(self, collection_file: CollectionFile):
        """Trigger indexing for a collection file"""
        from apps.documents.tasks import index_collection_files_task

        # Queue the file for indexing
        index_collection_files_task.delay([collection_file.id])


def sync_document_source(document_source: DocumentSource) -> SyncResult:
    """
    Convenience function to sync a document source.

    Args:
        document_source: DocumentSource instance to sync

    Returns:
        SyncResult with sync statistics and status
    """
    manager = DocumentSourceManager(document_source)
    return manager.sync_collection()


def sync_all_auto_enabled_sources() -> list[SyncResult]:
    """
    Sync all document sources that have auto_sync_enabled=True.

    Returns:
        List of SyncResult objects for each synced source
    """
    results = []

    auto_sources = DocumentSource.objects.filter(
        auto_sync_enabled=True,
        collection__is_index=True,  # Only sync indexed collections
    ).select_related("collection", "collection__team")

    for source in auto_sources:
        try:
            result = sync_document_source(source)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to sync document source {source.id}: {str(e)}")
            results.append(SyncResult(success=False, error_message=str(e)))

    return results

import logging
import time

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from langchain_core.documents import Document

from apps.documents.datamodels import ChunkingStrategy, CollectionFileMetadata
from apps.documents.models import (
    CollectionFile,
    DocumentSource,
    DocumentSourceSyncLog,
    FileStatus,
    SyncStatus,
)
from apps.documents.source_loaders.base import SyncResult
from apps.documents.source_loaders.registry import create_loader
from apps.documents.utils import bulk_delete_collection_files
from apps.files.models import File

logger = logging.getLogger("ocs.document_source")


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

            loader = create_loader(self.collection, self.document_source)

            logger.info(f"Loading documents from {self.document_source.source_type} source")
            result = self._sync_documents(loader)

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
            logger.exception(
                "Document Source Sync failed",
                extra={
                    "document_source_id": self.document_source.id,
                },
            )

            if sync_log:
                sync_log.status = SyncStatus.FAILED
                sync_log.error_message = error_msg
                sync_log.duration_seconds = duration
                sync_log.save()

            return SyncResult(success=False, error_message=error_msg, duration_seconds=duration)

    def _sync_documents(self, loader) -> SyncResult:
        """
        Sync documents with the collection, handling additions, updates, and removals.

        Args:
            loader: Document loader instance

        Returns:
            SyncResult with statistics
        """
        result = SyncResult(success=True)
        documents = loader.load_documents()

        with transaction.atomic():
            existing_files = CollectionFile.objects.filter(
                collection=self.collection, document_source=self.document_source
            ).select_related("file")

            existing_files_map = {}
            for file in existing_files:
                if file.external_id:
                    existing_files_map[file.external_id] = file

            seen_identifiers = set()

            files_to_index = []
            for document in documents:
                identifier = loader.get_document_identifier(document)
                seen_identifiers.add(identifier)

                if identifier in existing_files_map:
                    existing_file = existing_files_map[identifier]
                    if loader.should_update_document(document, existing_file):
                        self._update_file(existing_file, document, identifier)
                        files_to_index.append(existing_file.id)
                        result.files_updated += 1
                else:
                    new_file = self._create_file(document, identifier)
                    files_to_index.append(new_file.id)
                    result.files_added += 1

            # Remove files that are no longer in the source
            files_to_remove = [
                file for identifier, file in existing_files_map.items() if identifier not in seen_identifiers
            ]

            if files_to_remove:
                self._remove_files(files_to_remove)
                result.files_removed += len(files_to_remove)

            if files_to_index:
                self._index_files(files_to_index)

        return result

    def _create_file(self, document: Document, identifier: str):
        """Create a new file from a document"""
        filename = self._extract_filename(document, identifier)
        content_file = ContentFile(document.page_content.encode("utf-8"))
        file = File.create(
            filename=filename,
            file_obj=content_file,
            team_id=self.document_source.team_id,
            metadata=document.metadata,
        )

        # Create CollectionFile relationship
        collection_file = CollectionFile.objects.create(
            collection=self.collection,
            document_source=self.document_source,
            file=file,
            status=FileStatus.PENDING,
            metadata=CollectionFileMetadata(chunking_strategy=ChunkingStrategy(chunk_size=800, chunk_overlap=400)),
            external_id=identifier,
        )
        return collection_file

    def _update_file(self, collection_file: CollectionFile, document: Document, identifier: str):
        """Update an existing file with new document content"""
        filename = self._extract_filename(document, identifier)
        content_file = ContentFile(document.page_content.encode("utf-8"), name=filename)
        existing_file = collection_file.file
        existing_file.file = content_file
        existing_file.content_size = content_file.size
        existing_file.metadata = document.metadata
        existing_file.save()

        collection_file.status = FileStatus.PENDING
        collection_file.save(update_fields=["status"])

    def _remove_files(self, connection_files: list[CollectionFile]):
        bulk_delete_collection_files(self.collection, connection_files)

    def _extract_filename(self, document: Document, identifier: str) -> str:
        """Extract a suitable filename from document metadata or identifier"""
        if path := document.metadata.get("path"):
            return path

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

        return "unknown_filename"

    def _index_files(self, file_ids: list[int]):
        """Trigger indexing for a collection file"""
        from apps.documents.tasks import index_collection_files_task

        index_collection_files_task.delay(file_ids)


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

import hashlib
import logging
import time
from urllib.parse import unquote

from django.core.files.base import ContentFile
from django.db import DatabaseError, transaction
from django.utils import timezone
from langchain_core.documents import Document

from apps.documents.datamodels import ChunkingStrategy, CollectionFileMetadata
from apps.documents.exceptions import DocumentSourceDeleted
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
from apps.files.models import File, FilePurpose

_EXTERNAL_ID_MAX_LENGTH = 255
_EXTERNAL_ID_HASH_LENGTH = 64  # SHA-256 hex digest length
_EXTERNAL_ID_PREFIX_LENGTH = _EXTERNAL_ID_MAX_LENGTH - _EXTERNAL_ID_HASH_LENGTH


def _safe_external_id(identifier: str) -> str:
    """Return identifier truncated to fit in CharField(max_length=255).

    If the identifier is too long, the first 191 characters are kept and a
    SHA-256 hex digest of the full identifier is appended (191 + 64 = 255),
    guaranteeing uniqueness without collisions.
    """
    if len(identifier) <= _EXTERNAL_ID_MAX_LENGTH:
        return identifier
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return identifier[:_EXTERNAL_ID_PREFIX_LENGTH] + digest


logger = logging.getLogger("ocs.document_source")

# Cap the per-file failure detail stored on the sync log to keep the record (and the UI) bounded.
MAX_FAILURE_DETAILS = 50


def _format_failures(failures: list[str]) -> str:
    """Render per-file failures for the sync log, truncating an unbounded list."""
    shown = failures[:MAX_FAILURE_DETAILS]
    lines = [f"{len(failures)} file(s) failed to process:", *shown]
    if len(failures) > MAX_FAILURE_DETAILS:
        lines.append(f"...and {len(failures) - MAX_FAILURE_DETAILS} more")
    return "\n".join(lines)


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
            self._ensure_source_exists()

            # Create sync log
            sync_log = self._save_or_abort(
                lambda: DocumentSourceSyncLog.objects.create(
                    document_source=self.document_source, status=SyncStatus.IN_PROGRESS
                )
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
            sync_log.files_failed = result.files_failed
            if result.failures:
                sync_log.error_message = _format_failures(result.failures)
            sync_log.duration_seconds = duration

            def _persist_success():
                sync_log.save()
                # Update document source last sync time
                self.document_source.last_sync = timezone.now()
                self.document_source.save(update_fields=["last_sync"])

            self._save_or_abort(_persist_success)

            result.duration_seconds = duration
            result.success = True

            logger.info(
                f"Sync completed: {result.files_added} added, "
                f"{result.files_updated} updated, {result.files_removed} removed, "
                f"{result.files_failed} failed"
            )

            return result

        except DocumentSourceDeleted:
            # The source was deleted mid-sync: abort the whole task rather than crash on a
            # dangling foreign key. Any partial work is irrelevant now that the source is gone.
            logger.warning(
                "DocumentSource was deleted during sync; aborting.",
                extra={"document_source_id": self.document_source.id},
            )
            raise

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
                # Preserve any per-file failure detail already recorded so a late failure
                # (saving the log or the source timestamp) doesn't erase it.
                if sync_log.error_message:
                    sync_log.error_message = f"{sync_log.error_message}\n\nSync error: {error_msg}"
                else:
                    sync_log.error_message = error_msg
                sync_log.duration_seconds = duration
                # If this save fails because the source was deleted, _save_or_abort raises
                # DocumentSourceDeleted, which propagates to abort the task.
                self._save_or_abort(sync_log.save)

            return SyncResult(success=False, error_message=error_msg, duration_seconds=duration)

    def _ensure_source_exists(self) -> None:
        """Abort the sync if the DocumentSource has been deleted (or archived) mid-run.

        Raises:
            DocumentSourceDeleted: if the source row is no longer visible.
        """
        if not DocumentSource.objects.filter(id=self.document_source.id).exists():
            raise DocumentSourceDeleted(self.document_source.id)

    def _save_or_abort(self, save_callable):
        """Run a DB write, converting a dangling-foreign-key failure into an abort.

        A save can fail if the source is deleted in the narrow window between an existence
        check and the write itself. When that happens the write raises a ``DatabaseError``
        (an ``IntegrityError`` for a bad insert, or "did not affect any rows" for a forced
        update); we re-check existence and, if the source is gone, raise
        ``DocumentSourceDeleted`` to abort. A genuine, unrelated DB error is re-raised.
        """
        try:
            return save_callable()
        except DatabaseError:
            self._ensure_source_exists()
            raise

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
        existing_files_map = self._map_existing_files()

        seen_identifiers = set()
        files_to_index = []
        for document in documents:
            # Abort promptly if the source was deleted mid-sync, rather than recording a
            # per-file failure for every remaining document against a dangling foreign key.
            self._ensure_source_exists()
            identifier = _safe_external_id(loader.get_document_identifier(document))
            # Mark as seen before processing so a file that fails to update is not treated
            # as removed from the source (and deleted) on this run.
            seen_identifiers.add(identifier)
            file_id = self._sync_document(document, identifier, loader, existing_files_map, result)
            if file_id is not None:
                files_to_index.append(file_id)

        self._remove_stale_files(existing_files_map, seen_identifiers, result)

        if files_to_index:
            self._index_files(files_to_index)

        return result

    def _map_existing_files(self) -> dict[str, CollectionFile]:
        """Map already-synced files by their source identifier."""
        existing_files = CollectionFile.objects.filter(
            collection=self.collection, document_source=self.document_source
        ).select_related("file")
        return {_safe_external_id(file.external_id): file for file in existing_files if file.external_id}

    def _sync_document(
        self, document: Document, identifier: str, loader, existing_files_map: dict, result: SyncResult
    ) -> int | None:
        """Create or update a single document, returning the file id to index (or None).

        A single bad document must not abort the whole sync: log it, record it, and
        carry on so the remaining files are still processed and indexed.
        """
        if not document.page_content.strip():
            msg = (
                "Skipping document with empty content "
                "(file may be a scanned/image-based document with no extractable text)"
            )
            logger.warning(
                msg,
                extra={"document_source_id": self.document_source.id, "identifier": identifier},
            )
            result.files_failed += 1
            result.failures.append(f"{identifier}: {msg}")
            return None

        try:
            if identifier in existing_files_map:
                existing_file = existing_files_map[identifier]
                if not loader.should_update_document(document, existing_file):
                    return None
                with transaction.atomic():
                    self._update_file(existing_file, document, identifier)
                result.files_updated += 1
                return existing_file.id

            with transaction.atomic():
                new_file = self._create_file(document, identifier)
            result.files_added += 1
            return new_file.id
        except Exception as exc:
            logger.exception(
                "Failed to process document during sync",
                extra={"document_source_id": self.document_source.id, "identifier": identifier},
            )
            result.files_failed += 1
            result.failures.append(f"{identifier}: {exc}")
            return None

    def _remove_stale_files(self, existing_files_map: dict, seen_identifiers: set, result: SyncResult) -> None:
        """Delete files that are no longer present in the source."""
        files_to_remove = [
            file for identifier, file in existing_files_map.items() if identifier not in seen_identifiers
        ]
        if files_to_remove:
            with transaction.atomic():
                self._remove_files(files_to_remove)
            result.files_removed += len(files_to_remove)

    def _create_file(self, document: Document, identifier: str):
        """Create a new file from a document"""
        filename = self._extract_filename(document, identifier)
        content_file = ContentFile(document.page_content.encode("utf-8"))
        file = File.create(
            filename=filename,
            file_obj=content_file,
            team_id=self.document_source.team_id,
            metadata=document.metadata,
            purpose=FilePurpose.COLLECTION,
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
        existing_file.name = filename
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

        # Try to get filename from metadata. `source` is often a URL, so decode any
        # percent-encoding (e.g. "%20" -> " ") to get a human-readable filename.
        if "source" in document.metadata:
            source = document.metadata["source"]
            if "/" in source:
                return unquote(source.split("/")[-1])
            return unquote(source)

        # Fall back to using part of the identifier
        if ":" in identifier:
            parts = identifier.split(":")
            if len(parts) > 2:
                return parts[-1] or "document.txt"

        return "unknown_filename"

    def _index_files(self, file_ids: list[int]):
        """Trigger indexing for a collection file"""
        from apps.documents.tasks import (  # noqa: PLC0415 - circular: documents.tasks imports document_source_service
            index_collection_files_task,
        )

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

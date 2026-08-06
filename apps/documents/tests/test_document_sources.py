from collections.abc import Iterator
from typing import Self
from unittest import mock
from unittest.mock import Mock, patch

import pytest
from field_audit.models import AuditAction

from apps.documents.datamodels import DocumentSourceConfig, GitHubSourceConfig, JSONCollectionSourceConfig
from apps.documents.document_source_service import (
    _EXTERNAL_ID_MAX_LENGTH,
    DocumentSourceManager,
    _safe_external_id,
)
from apps.documents.exceptions import DocumentSourceDeleted
from apps.documents.models import (
    Collection,
    CollectionFile,
    DocumentSource,
    DocumentSourceSyncLog,
    FileStatus,
    SourceType,
    SyncStatus,
)
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument, SyncResult
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.files.models import File
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


@pytest.fixture()
def collection(team):
    return Collection.objects.create(name="Test Collection", team=team, is_index=True)


@pytest.fixture()
def github_config():
    return GitHubSourceConfig(
        repo_url="https://github.com/test/repo", branch="main", file_pattern="*.md", path_filter=""
    )


@pytest.fixture()
def document_source(collection, github_config):
    return DocumentSource.objects.create(
        collection=collection,
        team=collection.team,
        source_type=SourceType.GITHUB,
        config=DocumentSourceConfig(github=github_config),
        auto_sync_enabled=True,
    )


class MockLoader(BaseDocumentLoader):
    def __init__(self, collection: Collection, mock_documents: list):
        super().__init__(collection, Mock(), Mock())
        self.mock_documents = mock_documents

    @classmethod
    def for_document_source(cls, collection, document_source) -> Self:
        return cls(
            collection,
            [
                SourceDocument(
                    content=b"# Test Document",
                    metadata={
                        "source": "test.md",
                        "sha": "abc123",
                        "source_type": "test",
                    },
                )
            ],
        )

    def load_documents(self) -> Iterator[SourceDocument]:
        return iter(self.mock_documents)

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
        new_sha = document.metadata.get("sha")
        old_sha = existing_file.file.metadata.get("sha")
        return new_sha != old_sha


@pytest.mark.django_db()
class TestDocumentSourceManager:
    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_collection_success(self, create_loader, collection, document_source):
        create_loader.return_value = MockLoader.for_document_source(collection, document_source)

        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        result = manager.sync_collection()

        assert result.success
        assert result.files_added == 1

        files = list(CollectionFile.objects.filter(collection=collection))
        assert len(files) == 1
        assert files[0].status == FileStatus.PENDING
        file = files[0].file
        assert file.name == "test.md"
        assert file.content_type == "text/plain"
        assert file.file.read() == b"# Test Document"
        assert "sha" in file.metadata

        manager._index_files.assert_called_once()

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_stores_source_bytes_and_sniffed_content_type(self, create_loader, collection, document_source):
        """A synced PDF is stored as the PDF the source served, not as extracted text.

        The filename keeps the source's extension either way, so storing text under a .pdf
        name produced a download that no PDF reader could open.
        """
        docs = [
            SourceDocument(
                content=PDF_BYTES,
                metadata={"source": "https://example.com/report.pdf", "sha": "1", "source_type": "test"},
            )
        ]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        result = manager.sync_collection()

        assert result.success
        file = CollectionFile.objects.get(collection=collection).file
        assert file.name == "report.pdf"
        assert file.file.read() == PDF_BYTES
        assert file.content_type == "application/pdf"
        assert file.content_size == len(PDF_BYTES)

    @patch("apps.documents.document_source_service.create_loader")
    def test_update_refreshes_content_type(self, create_loader, collection, document_source):
        """An update rewrites the bytes, so a stale content type would misdescribe them."""
        url = "https://example.com/doc.pdf"
        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=b"just text", metadata={"source": url, "sha": "v1", "source_type": "test"})],
        )
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager.sync_collection()

        file = CollectionFile.objects.get(collection=collection).file
        assert file.content_type == "text/plain"

        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=PDF_BYTES, metadata={"source": url, "sha": "v2", "source_type": "test"})],
        )
        result = manager.sync_collection()

        assert result.files_updated == 1
        file.refresh_from_db()
        assert file.file.read() == PDF_BYTES
        assert file.content_type == "application/pdf"
        assert file.content_size == len(PDF_BYTES)

    @patch("apps.documents.document_source_service.create_loader")
    def test_update_discards_the_remote_copy_of_the_old_bytes(self, create_loader, collection, document_source):
        """An update can change the format outright, not just the text.

        The remote index skips re-uploading a file whose external id still resolves, so
        keeping the id would leave the previous bytes indexed under the new content type.
        """
        url = "https://example.com/doc.pdf"
        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=b"just text", metadata={"source": url, "sha": "v1", "source_type": "test"})],
        )
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager.sync_collection()

        file = CollectionFile.objects.get(collection=collection).file
        File.objects.filter(id=file.id).update(external_id="openai-file-123", external_source="openai")

        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=PDF_BYTES, metadata={"source": url, "sha": "v2", "source_type": "test"})],
        )
        manager.sync_collection()

        file.refresh_from_db()
        assert file.external_id == ""
        assert file.external_source == ""

    @patch("apps.documents.document_source_service.create_loader")
    def test_update_keeps_the_extension_create_added(self, create_loader, collection, document_source):
        """``File.create`` appends an extension when the source gives none, and the reader
        and download paths fall back on it. An update must not strip it back off."""
        url = "https://example.com/files/report"
        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=PDF_BYTES, metadata={"source": url, "sha": "v1", "source_type": "test"})],
        )
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager.sync_collection()

        file = CollectionFile.objects.get(collection=collection).file
        assert file.name == "report.pdf"

        create_loader.return_value = MockLoader(
            collection,
            [SourceDocument(content=PDF_BYTES, metadata={"source": url, "sha": "v2", "source_type": "test"})],
        )
        manager.sync_collection()

        file.refresh_from_db()
        assert file.name == "report.pdf"

    @patch("apps.documents.document_source_service.create_loader")
    def test_empty_payload_is_recorded_as_failed(self, create_loader, collection, document_source):
        """A source that serves nothing has nothing to index; record it and carry on."""
        docs = [
            SourceDocument(content=b"", metadata={"source": "empty.pdf", "sha": "1", "source_type": "test"}),
            SourceDocument(content=b"real content", metadata={"source": "ok.md", "sha": "2", "source_type": "test"}),
        ]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        result = manager.sync_collection()

        assert result.files_added == 1
        assert result.files_failed == 1
        assert any("empty.pdf" in failure for failure in result.failures)
        names = set(CollectionFile.objects.filter(collection=collection).values_list("file__name", flat=True))
        assert names == {"ok.md"}

    @patch("apps.documents.document_source_service.create_loader")
    def test_whitespace_only_payload_is_stored_not_skipped(self, create_loader, collection, document_source):
        """The source served bytes, so store them (ADR-0051) and let indexing judge the text.

        Skipping at sync time would leave a previously synced file in place with stale content,
        since the identifier is already marked as seen and so escapes stale-file removal.
        """
        docs = [
            SourceDocument(content=b"  \n  ", metadata={"source": "blank.txt", "sha": "1", "source_type": "test"}),
        ]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        result = manager.sync_collection()

        assert result.files_added == 1
        assert result.files_failed == 0
        names = set(CollectionFile.objects.filter(collection=collection).values_list("file__name", flat=True))
        assert names == {"blank.txt"}

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_collection_update_existing(self, create_loader, collection, document_source):
        create_loader.return_value = MockLoader.for_document_source(collection, document_source)

        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager._update_file = Mock(wraps=manager._update_file)
        result = manager.sync_collection()

        assert result.success
        assert result.files_added == 1
        manager._update_file.assert_not_called()
        manager._index_files.assert_called_once()
        manager._index_files.reset_mock()

        # 2nd call with same files doesn't change anything
        result = manager.sync_collection()
        assert result.success
        assert result.files_added == 0
        assert result.files_updated == 0
        assert result.files_removed == 0
        manager._update_file.assert_not_called()
        manager._index_files.assert_not_called()

        mock_docs = [
            SourceDocument(
                content=b"# Test Document updated",
                metadata={"source": "test.md", "sha": "abc1234", "source_type": "test"},
            )
        ]
        create_loader.return_value = MockLoader(collection, mock_docs)
        # 3rd call with updated file calls update
        result = manager.sync_collection()
        assert result.success
        assert result.files_added == 0
        assert result.files_updated == 1
        assert result.files_removed == 0
        manager._update_file.assert_called_once()
        manager._index_files.assert_called_once()

        files = list(CollectionFile.objects.filter(collection=collection))
        assert len(files) == 1
        assert files[0].status == FileStatus.PENDING
        file = files[0].file
        assert file.file.read() == b"# Test Document updated"

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_collection_delete_file(self, create_loader, collection, document_source):
        create_loader.return_value = MockLoader.for_document_source(collection, document_source)

        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager._remove_files = Mock()
        result = manager.sync_collection()

        assert result.success
        assert result.files_added == 1
        manager._index_files.reset_mock()

        # 2nd call with removed file
        create_loader.return_value = MockLoader(collection, [])
        result = manager.sync_collection()
        assert result.success
        assert result.files_added == 0
        assert result.files_updated == 0
        assert result.files_removed == 1
        manager._remove_files.assert_called_once()
        manager._index_files.assert_not_called()

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_truncates_long_external_id_and_is_stable(self, create_loader, collection, document_source):
        """A >255-char identifier is truncated on store and matches on re-sync (no DataError, no churn)."""
        long_source = "https://example.com/" + "a" * 300
        docs = [
            SourceDocument(
                content=b"content",
                metadata={"path": "doc.md", "source": long_source, "sha": "1", "source_type": "test"},
            )
        ]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        result = manager.sync_collection()
        assert result.success
        assert result.files_added == 1

        collection_file = CollectionFile.objects.get(collection=collection)
        assert len(collection_file.external_id) <= _EXTERNAL_ID_MAX_LENGTH
        assert collection_file.external_id == _safe_external_id(long_source)

        # Re-syncing the same document must reuse the existing record: no add, update, or delete.
        create_loader.return_value = MockLoader(collection, docs)
        result = manager.sync_collection()
        assert result.success
        assert result.files_added == 0
        assert result.files_updated == 0
        assert result.files_removed == 0
        assert CollectionFile.objects.filter(collection=collection).count() == 1

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_continues_when_a_file_fails(self, create_loader, collection, document_source):
        """A single document that fails to process is recorded but does not abort the sync."""
        docs = [
            SourceDocument(content=b"doc1", metadata={"source": "a.md", "sha": "1", "source_type": "test"}),
            SourceDocument(content=b"doc2", metadata={"source": "b.md", "sha": "2", "source_type": "test"}),
            SourceDocument(content=b"doc3", metadata={"source": "c.md", "sha": "3", "source_type": "test"}),
        ]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        real_create = manager._create_file

        def create_side_effect(document, identifier):
            if identifier == "b.md":
                raise RuntimeError("boom")
            return real_create(document, identifier)

        manager._create_file = Mock(side_effect=create_side_effect)

        result = manager.sync_collection()

        assert result.success
        assert result.files_added == 2
        assert result.files_failed == 1
        assert any("b.md" in failure for failure in result.failures)

        # Only the successful files are persisted and handed to indexing.
        names = set(CollectionFile.objects.filter(collection=collection).values_list("file__name", flat=True))
        assert names == {"a.md", "c.md"}
        manager._index_files.assert_called_once()
        assert len(manager._index_files.call_args[0][0]) == 2

        log = DocumentSourceSyncLog.objects.filter(document_source=document_source).first()
        assert log.status == SyncStatus.SUCCESS
        assert log.files_failed == 1
        assert log.completed_with_errors is True
        assert "b.md" in log.error_message

    @patch("apps.documents.document_source_service.create_loader")
    def test_late_failure_preserves_per_file_detail(self, create_loader, collection, document_source):
        """A failure after per-file errors are recorded must not erase the detail."""
        create_loader.return_value = MockLoader.for_document_source(collection, document_source)
        manager = DocumentSourceManager(document_source)

        with (
            patch.object(manager, "_sync_documents") as mock_sync,
            patch.object(document_source, "save", side_effect=RuntimeError("db down")),
        ):
            mock_sync.return_value = SyncResult(success=True, files_added=1, files_failed=1, failures=["b.md: boom"])
            result = manager.sync_collection()

        assert result.success is False
        log = DocumentSourceSyncLog.objects.filter(document_source=document_source).latest("sync_date")
        assert log.status == SyncStatus.FAILED
        assert "b.md" in log.error_message
        assert "db down" in log.error_message

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(
                "https://example.com/files/Loa%20Loa_bringing%20an%20end.pdf",
                "Loa Loa_bringing an end.pdf",
                id="url-encoded-path",
            ),
            pytest.param("https://example.com/plain.pdf", "plain.pdf", id="plain-path"),
            pytest.param("just_a_name.pdf", "just_a_name.pdf", id="no-slash"),
        ],
    )
    def test_extract_filename_url_decodes_source(self, document_source, source, expected):
        manager = DocumentSourceManager(document_source)
        document = SourceDocument(content=b"x", metadata={"source": source})
        assert manager._extract_filename(document, source) == expected

    @patch("apps.documents.document_source_service.create_loader")
    def test_update_file_refreshes_decoded_name(self, create_loader, collection, document_source):
        url = "https://example.com/files/My%20Doc.pdf"
        docs = [SourceDocument(content=b"v1", metadata={"source": url, "sha": "v1", "source_type": "test"})]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager.sync_collection()

        updated_docs = [SourceDocument(content=b"v2", metadata={"source": url, "sha": "v2", "source_type": "test"})]
        create_loader.return_value = MockLoader(collection, updated_docs)
        manager.sync_collection()

        file = CollectionFile.objects.get(collection=collection).file
        assert file.name == "My Doc.pdf"
        assert file.file.read() == b"v2"

    @patch("apps.documents.document_source_service.create_loader")
    def test_update_file_clears_stale_failure_reason(self, create_loader, collection, document_source):
        """A re-sync of a previously failed file must clear failure_reason along with status,
        otherwise the pending badge still shows the old error in its tooltip until the Celery
        indexing task eventually clears it.
        """
        url = "https://example.com/files/doc.pdf"
        docs = [SourceDocument(content=b"v1", metadata={"source": url, "sha": "v1", "source_type": "test"})]
        create_loader.return_value = MockLoader(collection, docs)
        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()
        manager.sync_collection()

        collection_file = CollectionFile.objects.get(collection=collection)
        collection_file.status = FileStatus.FAILED
        collection_file.failure_reason = "ValueError: old error"
        collection_file.save(update_fields=["status", "failure_reason"])

        updated_docs = [SourceDocument(content=b"v2", metadata={"source": url, "sha": "v2", "source_type": "test"})]
        create_loader.return_value = MockLoader(collection, updated_docs)
        manager.sync_collection()

        collection_file.refresh_from_db()
        assert collection_file.status == FileStatus.PENDING
        assert collection_file.failure_reason == ""

    def test_sync_log_created(self, document_source):
        initial_count = DocumentSourceSyncLog.objects.count()

        manager = DocumentSourceManager(document_source)

        with patch.object(manager, "_sync_documents") as mock_sync:
            mock_sync.return_value = SyncResult(success=True, files_added=1)

            with patch("apps.documents.document_source_service.create_loader") as mock_create_loader:
                mock_create_loader.return_value = MockLoader.for_document_source(
                    document_source.collection, document_source
                )
                result = manager.sync_collection()

        assert result.success
        assert DocumentSourceSyncLog.objects.count() == initial_count + 1

        sync_log = DocumentSourceSyncLog.objects.latest("sync_date")
        assert sync_log.document_source == document_source
        assert sync_log.status == SyncStatus.SUCCESS

    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_aborts_when_source_deleted_mid_sync(self, create_loader, collection, document_source):
        """Deleting the source mid-sync aborts the whole run instead of crashing on a dangling FK.

        Regression: the source is fetched once at the start of a long sync; if a user deletes
        it before the sync persists its results, writes against the now-missing foreign key
        used to raise an unhandled IntegrityError/DatabaseError and crash the task.
        """
        source_id = document_source.id

        class _DeletingLoader(MockLoader):
            def load_documents(self) -> Iterator[SourceDocument]:
                # Simulate a user deleting the source while documents are being loaded.
                DocumentSource.objects.filter(id=source_id).delete(audit_action=AuditAction.AUDIT)
                yield SourceDocument(
                    content=b"# Doc", metadata={"source": "test.md", "sha": "abc", "source_type": "test"}
                )

        loader = _DeletingLoader(collection, [])
        create_loader.return_value = loader

        manager = DocumentSourceManager(document_source)
        manager._index_files = Mock()

        with pytest.raises(DocumentSourceDeleted):
            manager.sync_collection()

        # Nothing is indexed and no orphaned files survive the aborted run.
        manager._index_files.assert_not_called()
        assert not CollectionFile.objects.filter(collection=collection).exists()

    def test_sync_aborts_when_source_already_gone(self, document_source):
        """If the source disappears before any documents load, sync aborts immediately."""
        manager = DocumentSourceManager(document_source)
        DocumentSource.objects.filter(id=document_source.id).delete(audit_action=AuditAction.AUDIT)

        with pytest.raises(DocumentSourceDeleted):
            manager.sync_collection()


@pytest.mark.django_db()
class TestJSONCollectionEndToEnd:
    def test_sync_creates_collection_files_for_each_document(self):
        collection = CollectionFactory.create(is_index=False)
        document_source = DocumentSourceFactory.create(
            collection=collection,
            config=DocumentSourceConfig(
                json_collection=JSONCollectionSourceConfig(json_url="https://example.com/feed.json"),
            ),
        )
        document_source.source_type = SourceType.JSON_COLLECTION
        document_source.save()

        fake_docs = [
            SourceDocument(
                content=b"text 1",
                metadata={
                    "source": "https://example.com/1.pdf",
                    "link": "https://example.com/1.pdf",
                    "title": "Doc 1",
                    "URI": "https://example.com/page1",
                    "date": "01/01/2025",
                },
            ),
            SourceDocument(
                content=b"text 2",
                metadata={
                    "source": "https://example.com/2.pdf",
                    "link": "https://example.com/2.pdf",
                    "title": "Doc 2",
                    "URI": "https://example.com/page2",
                    "date": "01/01/2025",
                },
            ),
        ]

        with (
            mock.patch.object(JSONCollectionLoader, "load_documents", return_value=iter(fake_docs)),
            mock.patch("apps.documents.document_source_service.DocumentSourceManager._index_files") as index_mock,
        ):
            result = DocumentSourceManager(document_source).sync_collection()

        assert result.success
        assert result.files_added == 2
        assert CollectionFile.objects.filter(collection=collection).count() == 2
        index_mock.assert_called_once()

from collections.abc import Iterator
from typing import Self
from unittest.mock import Mock, patch

import pytest
from langchain_core.documents import Document

from apps.documents.document_source_service import DocumentSourceManager
from apps.documents.models import (
    Collection,
    CollectionFile,
    DocumentSource,
    DocumentSourceConfig,
    DocumentSourceSyncLog,
    FileStatus,
    GitHubSourceConfig,
    SourceType,
    SyncStatus,
)
from apps.documents.source_loaders.base import BaseDocumentLoader


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
    @classmethod
    def for_document_source(cls, collection, document_source) -> Self:
        return cls(collection, Mock(), Mock())

    def load_documents(self) -> Iterator[Document]:
        return iter([
            Mock(page_content="# Test Document", metadata={
                "source": "test.md", "sha": "abc123", "source_type": "test",
            })
        ])


@pytest.mark.django_db()
class TestDocumentSourceManager:
    @patch("apps.documents.document_source_service.create_loader")
    def test_sync_collection_success(self, create_loader, document_source):
        collection = document_source.collection
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
        assert file.content_type == "text/markdown"
        assert file.file.read() == b"# Test Document"

        manager._index_files.assert_called_once()

    def test_sync_log_created(self, document_source):
        initial_count = DocumentSourceSyncLog.objects.count()

        manager = DocumentSourceManager(document_source)

        with patch.object(manager, "_sync_documents") as mock_sync:
            from apps.documents.source_loaders.base import SyncResult

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

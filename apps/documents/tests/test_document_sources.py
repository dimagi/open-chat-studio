from unittest.mock import Mock, patch

import pytest

from apps.documents.document_source_service import DocumentSourceManager
from apps.documents.models import (
    Collection,
    DocumentSource,
    DocumentSourceConfig,
    DocumentSourceSyncLog,
    GitHubSourceConfig,
    SourceType,
    SyncStatus,
)
from apps.documents.source_loaders.github import GitHubDocumentLoader
from apps.teams.models import Team


@pytest.fixture()
def team():
    return Team.objects.create(name="Test Team", slug="test-team")


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


@pytest.mark.django_db()
class TestGitHubDocumentLoader:
    def test_validate_config_valid(self, github_config, collection):
        loader = GitHubDocumentLoader(github_config, collection)
        is_valid, error = loader.validate_config()
        assert is_valid
        assert error == ""

    def test_validate_config_missing_repo_url(self, collection):
        config = GitHubSourceConfig(repo_url="", branch="main", file_pattern="*.md")
        loader = GitHubDocumentLoader(config, collection)
        is_valid, error = loader.validate_config()
        assert not is_valid
        assert "Repository URL is required" in error

    def test_extract_repo_info(self, github_config, collection):
        loader = GitHubDocumentLoader(github_config, collection)
        owner, repo = loader._extract_repo_info()
        assert owner == "test"
        assert repo == "repo"

    def test_extract_repo_info_ssh_url(self, collection):
        config = GitHubSourceConfig(repo_url="git@github.com:test/repo.git", branch="main", file_pattern="*.md")
        loader = GitHubDocumentLoader(config, collection)
        owner, repo = loader._extract_repo_info()
        assert owner == "test"
        assert repo == "repo"

    def test_matches_pattern(self, github_config, collection):
        loader = GitHubDocumentLoader(github_config, collection)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("docs/guide.md")
        assert not loader._matches_pattern("script.py")
        assert not loader._matches_pattern("README.txt")

    def test_matches_multiple_patterns(self, collection):
        config = GitHubSourceConfig(
            repo_url="https://github.com/test/repo", branch="main", file_pattern="*.md, *.txt, *.py"
        )
        loader = GitHubDocumentLoader(config, collection)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("notes.txt")
        assert loader._matches_pattern("script.py")
        assert not loader._matches_pattern("image.png")


@pytest.mark.django_db()
class TestDocumentSourceManager:
    @patch("apps.documents.source_loaders.github.GithubFileLoader")
    def test_sync_collection_success(self, mock_github_loader, document_source):
        # Mock the GithubFileLoader
        mock_loader_instance = Mock()
        mock_loader_instance.load.return_value = [
            Mock(page_content="# Test Document", metadata={"source": "test.md", "sha": "abc123"})
        ]
        mock_github_loader.return_value = mock_loader_instance

        manager = DocumentSourceManager(document_source)
        result = manager.sync_collection()

        assert result.success
        assert result.files_added == 1

    def test_sync_log_created(self, document_source):
        initial_count = DocumentSourceSyncLog.objects.count()

        manager = DocumentSourceManager(document_source)

        # Mock the sync to avoid external API calls
        with patch.object(manager, "_sync_documents") as mock_sync:
            from apps.documents.source_loaders.base import SyncResult

            mock_sync.return_value = SyncResult(success=True, files_added=1)

            with patch("apps.documents.source_loaders.registry.create_loader") as mock_create_loader:
                mock_loader = Mock()
                mock_loader.validate_config.return_value = (True, "")
                mock_loader.load_documents.return_value = []
                mock_create_loader.return_value = mock_loader

                result = manager.sync_collection()

        assert result.success
        assert DocumentSourceSyncLog.objects.count() == initial_count + 1

        sync_log = DocumentSourceSyncLog.objects.latest("sync_date")
        assert sync_log.document_source == document_source
        assert sync_log.status == SyncStatus.SUCCESS


@pytest.mark.django_db()
class TestDocumentSourceModel:
    def test_str_representation(self, document_source):
        expected = f"GitHub Repository source for {document_source.collection.name}"
        assert str(document_source) == expected

    def test_source_config_property(self, document_source, github_config):
        assert document_source.source_config == github_config

    def test_unique_constraint(self, collection, github_config):
        # Create first document source
        DocumentSource.objects.create(
            collection=collection,
            team=collection.team,
            source_type=SourceType.GITHUB,
            config=DocumentSourceConfig(github=github_config),
        )

        # Attempting to create another should raise an integrity error
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            DocumentSource.objects.create(
                collection=collection,
                team=collection.team,
                source_type=SourceType.CONFLUENCE,
                config=DocumentSourceConfig(),
            )


@pytest.mark.django_db()
class TestDocumentSourceSyncLog:
    def test_str_representation(self, document_source):
        sync_log = DocumentSourceSyncLog.objects.create(
            document_source=document_source, status=SyncStatus.SUCCESS, files_added=5, files_updated=2, files_removed=1
        )

        expected = f"Sync of {document_source} on {sync_log.sync_date}"
        assert str(sync_log) == expected

    def test_total_files_processed(self, document_source):
        sync_log = DocumentSourceSyncLog.objects.create(
            document_source=document_source, status=SyncStatus.SUCCESS, files_added=5, files_updated=2, files_removed=1
        )

        assert sync_log.total_files_processed == 8

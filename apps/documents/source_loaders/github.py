import fnmatch
import logging
from collections.abc import Iterator
from typing import Self

from langchain_community.document_loaders.github import GithubFileLoader
from langchain_core.documents import Document

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)


class GitHubDocumentLoader(BaseDocumentLoader[GitHubSourceConfig]):
    """Document loader for GitHub repositories"""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        auth_provider = document_source.auth_provider
        if not auth_provider or auth_provider.type != AuthProviderType.bearer:
            type_ = auth_provider.type if auth_provider else "None"
            raise ValueError(f"GitHub document source requires bearer authentication, got {type_}")
        if not auth_provider.config.get("token"):
            raise ValueError("GitHub authentication token is missing")
        return cls(collection, document_source.config.github, auth_provider)

    def load_documents(self) -> Iterator[Document]:
        """Load documents from GitHub repository"""
        try:
            owner, repo = self.config.extract_repo_info()

            # Create the GithubFileLoader
            loader = GithubFileLoader(
                repo=f"{owner}/{repo}",
                access_token=self.auth_provider.config.get("token"),
                branch=self.config.branch,
                file_filter=self._matches_pattern,
            )

            # Load documents
            for document in loader.lazy_load():
                document.metadata.update(
                    {
                        "collection_id": self.collection.id,
                        "source_type": "github",
                        "repo_url": str(self.config.repo_url),
                        "branch": self.config.branch,
                    }
                )
                yield document

        except Exception as e:
            logger.error(f"Error loading documents from GitHub: {str(e)}")
            raise

    def _matches_pattern(self, file_path: str) -> bool:
        """Check if the file path matches the configured filters"""
        if self.config.path_filter and not file_path.startswith(self.config.path_filter):
            return False
        patterns = [p.strip() for p in self.config.file_pattern.split(",")]
        include_patterns = [p for p in patterns if not p.startswith("!")]
        exclude_patterns = [p[1:] for p in patterns if p.startswith("!")]
        return any(fnmatch.fnmatch(file_path, pattern) for pattern in include_patterns) and not any(
            fnmatch.fnmatch(file_path, pattern) for pattern in exclude_patterns
        )

    def should_update_document(self, document: Document, existing_file: CollectionFile) -> bool:
        """
        Determine if document should be updated.
        For GitHub, we can use commit hash or last modified time if available.
        """
        # Check if commit hash changed (if available in metadata)
        new_commit = document.metadata.get("sha")
        old_commit = existing_file.file.metadata.get("sha")

        if new_commit and old_commit:
            return new_commit != old_commit
        return super().should_update_document(document, existing_file)

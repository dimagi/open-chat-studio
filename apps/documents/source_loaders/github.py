import fnmatch
import logging
from typing import Any, Iterator

from langchain_community.document_loaders.github import GithubFileLoader
from langchain_core.documents import Document

from apps.documents.models import Collection, CollectionFile, GitHubSourceConfig
from apps.documents.source_loaders.base import BaseDocumentLoader

logger = logging.getLogger(__name__)


class GitHubDocumentLoader(BaseDocumentLoader):
    """Document loader for GitHub repositories"""

    def __init__(self, config: GitHubSourceConfig, collection: Collection):
        super().__init__(config.model_dump(), collection)
        self.github_config = config

    def load_documents(self) -> Iterator[Document]:
        """Load documents from GitHub repository"""
        try:
            owner, repo = self.github_config.extract_repo_info()

            # Create the GithubFileLoader
            loader = GithubFileLoader(
                repo=f"{owner}/{repo}",
                branch=self.github_config.branch,
                file_filter=self._matches_pattern,
            )

            # Load documents
            for document in loader.lazy_load():
                document.metadata.update(
                    {
                        "collection_id": self.collection.id,
                        "source_type": "github",
                        "repo_url": self.github_config.repo_url,
                        "branch": self.github_config.branch,
                    }
                )
                yield document

        except Exception as e:
            logger.error(f"Error loading documents from GitHub: {str(e)}")
            raise

    def _matches_pattern(self, file_path: str) -> bool:
        """Check if the file path matches the configured filters"""
        if self.github_config.path_filter and not file_path.startswith(self.github_config.path_filter):
            return False
        patterns = [p.strip() for p in self.github_config.file_pattern.split(",")]
        return any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)

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

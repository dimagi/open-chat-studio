import fnmatch
import logging
from typing import Any
from urllib.parse import urlparse

from langchain_community.document_loaders.github import GithubFileLoader
from langchain_core.documents import Document

from apps.documents.models import Collection, GitHubSourceConfig
from apps.documents.source_loaders.base import BaseDocumentLoader

logger = logging.getLogger(__name__)


class GitHubDocumentLoader(BaseDocumentLoader):
    """Document loader for GitHub repositories"""

    def __init__(self, config: GitHubSourceConfig, collection: Collection):
        super().__init__(config.model_dump(), collection)
        self.github_config = config

    def validate_config(self) -> tuple[bool, str]:
        """Validate GitHub configuration"""
        if not self.github_config.repo_url:
            return False, "Repository URL is required"

        # Basic URL validation
        try:
            parsed = urlparse(self.github_config.repo_url)
            if not parsed.netloc or not parsed.path:
                return False, "Invalid repository URL format"
        except Exception as e:
            return False, f"Invalid repository URL: {str(e)}"

        if not self.github_config.file_pattern:
            return False, "File pattern is required"

        return True, ""

    def _extract_repo_info(self) -> tuple[str, str]:
        """Extract owner and repo name from GitHub URL"""
        url = self.github_config.repo_url.rstrip("/")

        # Handle different GitHub URL formats
        if url.startswith("https://github.com/"):
            path = url.replace("https://github.com/", "")
        elif url.startswith("git@github.com:"):
            path = url.replace("git@github.com:", "").replace(".git", "")
        else:
            # Assume it's already in owner/repo format
            path = url

        parts = path.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        else:
            raise ValueError(f"Unable to extract owner/repo from URL: {url}")

    def load_documents(self) -> list[Document]:
        """Load documents from GitHub repository"""
        try:
            owner, repo = self._extract_repo_info()

            # Create the GithubFileLoader
            loader = GithubFileLoader(
                repo=f"{owner}/{repo}",
                branch=self.github_config.branch,
                file_filter=lambda file_path: self._matches_pattern(file_path),
            )

            # Load documents
            documents = loader.load()

            # Filter by path if specified
            if self.github_config.path_filter:
                documents = [
                    doc
                    for doc in documents
                    if doc.metadata.get("source", "").startswith(self.github_config.path_filter)
                ]

            # Add additional metadata
            for doc in documents:
                doc.metadata.update(
                    {
                        "collection_id": self.collection.id,
                        "source_type": "github",
                        "repo_url": self.github_config.repo_url,
                        "branch": self.github_config.branch,
                    }
                )

            logger.info(f"Loaded {len(documents)} documents from GitHub repo {owner}/{repo}")
            return documents

        except Exception as e:
            logger.error(f"Error loading documents from GitHub: {str(e)}")
            raise

    def _matches_pattern(self, file_path: str) -> bool:
        """Check if file path matches the configured pattern"""
        patterns = [p.strip() for p in self.github_config.file_pattern.split(",")]
        return any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)

    def get_source_metadata(self) -> dict[str, Any]:
        """Get metadata about the GitHub source"""
        owner, repo = self._extract_repo_info()

        return {
            "source_type": "github",
            "owner": owner,
            "repo": repo,
            "branch": self.github_config.branch,
            "file_pattern": self.github_config.file_pattern,
            "path_filter": self.github_config.path_filter,
            "repo_url": self.github_config.repo_url,
        }

    def get_document_identifier(self, document: Document) -> str:
        """Get unique identifier for a GitHub document"""
        # Use the file path within the repo as the identifier
        source = document.metadata.get("source", "")
        return f"github:{self.github_config.repo_url}:{self.github_config.branch}:{source}"

    def should_update_document(self, document: Document, existing_metadata: dict) -> bool:
        """
        Determine if document should be updated.
        For GitHub, we can use commit hash or last modified time if available.
        """
        # Check if commit hash changed (if available in metadata)
        new_commit = document.metadata.get("sha")
        old_commit = existing_metadata.get("sha")

        if new_commit and old_commit:
            return new_commit != old_commit

        # Fall back to default behavior
        return super().should_update_document(document, existing_metadata)

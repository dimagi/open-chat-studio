import fnmatch
import logging
from collections.abc import Iterator
from typing import Self
from urllib.parse import quote

import httpx
from django.conf import settings
from langchain_community.document_loaders.github import GithubFileLoader

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)

# GitHub will serve a blob up to 100 MB, well past what a collection file may be, so an
# oversized one is dropped rather than downloaded and then refused.
MAX_BLOB_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024
REQUEST_TIMEOUT = 30


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

    def load_documents(self) -> Iterator[SourceDocument]:
        """Load documents from GitHub repository.

        Only the repo listing comes from ``GithubFileLoader``. Its ``lazy_load`` is not used
        because it decodes every blob as UTF-8: one file that is not UTF-8 text -- a binary
        asset, a latin-1 source file -- raised UnicodeDecodeError out of the generator and
        aborted the whole sync, including the files after it. Fetching the blob as bytes has
        no decode to fail, and bytes are what gets stored anyway (ADR-0051).
        """
        try:
            owner, repo = self.config.extract_repo_info()

            # Create the GithubFileLoader
            loader = GithubFileLoader(
                repo=f"{owner}/{repo}",
                access_token=self.auth_provider.config.get("token"),
                branch=self.config.branch,
                file_filter=self._matches_pattern,
            )

            for entry in loader.get_file_paths():
                document = self._load_blob(loader, entry)
                if document is not None:
                    yield document

        except Exception as e:
            logger.error(f"Error loading documents from GitHub: {str(e)}")
            raise

    def _load_blob(self, loader: GithubFileLoader, entry: dict) -> SourceDocument | None:
        """Fetch one listing entry, or None if it is not a file this sync can carry."""
        # The tree lists directories and submodule pointers alongside files; neither has
        # content to fetch.
        if entry.get("type") != "blob":
            return None

        path = entry["path"]
        size = entry.get("size")
        if size is not None and size > MAX_BLOB_BYTES:
            logger.warning(
                "Skipping %s: %s bytes exceeds the %s byte limit for a collection file",
                path,
                size,
                MAX_BLOB_BYTES,
            )
            return None

        metadata = {
            "path": path,
            "sha": entry["sha"],
            "collection_id": self.collection.id,
            "source_type": "github",
            "repo_url": str(self.config.repo_url),
            "branch": self.config.branch,
            # The web URL, not the API one: this is both the citation link and the identifier
            # that matches a document to its already-synced file.
            "source": f"{self.config.repo_url}/blob/{self.config.branch}/{path}",
        }
        return SourceDocument(content=self._fetch_blob(loader, path), metadata=metadata)

    def _fetch_blob(self, loader: GithubFileLoader, path: str) -> bytes:
        """Download a single file as the bytes GitHub stores.

        The raw media type returns the file itself rather than the JSON envelope, whose
        base64 ``content`` field is empty for anything over 1 MB.
        """
        url = f"{loader.github_api_url}/repos/{loader.repo}/contents/{quote(path)}"
        params = {"ref": loader.branch} if loader.branch else None
        headers = {**loader.headers, "Accept": "application/vnd.github.raw"}
        with httpx.stream(
            "GET", url, params=params, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as response:
            response.raise_for_status()
            # The size in the listing is the API's claim; the read is bounded on its own so a
            # wrong or missing one cannot pull a whole repo into the worker's memory.
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_BLOB_BYTES:
                    raise ValueError(f"{path} exceeds the {MAX_BLOB_BYTES} byte cap")
                chunks.append(chunk)
            return b"".join(chunks)

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

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
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

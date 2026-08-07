import fnmatch
import logging
from collections.abc import Iterator
from typing import Self
from urllib.parse import quote

import httpx
from django.conf import settings

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.documents.source_loaders.http import ResponseTooLarge, read_capped
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT = 30


def _max_blob_bytes() -> int:
    """The largest blob a sync will carry. Read per call so the setting stays overridable."""
    return settings.MAX_FILE_SIZE_MB * 1024 * 1024


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

        Blobs are fetched as bytes rather than decoded text. The langchain loader this
        replaces decoded every blob as UTF-8, so one file that is not UTF-8 text -- a binary
        asset, a latin-1 source file -- raised UnicodeDecodeError out of the generator and
        aborted the whole sync, taking the files after it down with it. There is no decode
        here to fail, and bytes are what gets stored anyway (ADR-0051).
        """
        try:
            max_bytes = _max_blob_bytes()
            # One client for the whole run: a repo sync is many small requests to a single
            # host, and a client per blob pays a fresh TCP and TLS handshake for each.
            # Authorization is safe to hold on the client -- httpx drops it on a
            # cross-origin redirect, and GitHub's raw redirect target is separately signed.
            with httpx.Client(
                base_url=GITHUB_API_URL,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                for entry in self._list_tree(client):
                    document = self._load_blob(client, entry, max_bytes)
                    if document is not None:
                        yield document

        except Exception as e:
            logger.error(f"Error loading documents from GitHub: {str(e)}")
            raise

    @property
    def _repo(self) -> str:
        owner, repo = self.config.extract_repo_info()
        return f"{owner}/{repo}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.auth_provider.config['token']}",
        }

    def _list_tree(self, client: httpx.Client) -> list[dict]:
        """List the repo entries matching the configured filters, at the configured branch.

        A truncated listing is refused rather than used. The trees API drops entries past its
        own limits and offers no pagination, and this sync deletes any already-synced file it
        does not see in the listing -- so quietly accepting a partial one prunes files that
        are still in the repo. Failing loudly leaves the collection as it was.
        """
        response = client.get(f"/repos/{self._repo}/git/trees/{quote(self.config.branch)}", params={"recursive": "1"})
        response.raise_for_status()
        listing = response.json()
        if listing.get("truncated"):
            raise ValueError(f"GitHub truncated the file listing for {self._repo}@{self.config.branch}")
        return [entry for entry in listing["tree"] if self._matches_pattern(entry["path"])]

    def _load_blob(self, client: httpx.Client, entry: dict, max_bytes: int) -> SourceDocument | None:
        """Fetch one listing entry, or None if it is not a file this sync can carry."""
        # The tree lists directories and submodule pointers alongside files; neither has
        # content to fetch.
        if entry.get("type") != "blob":
            return None

        path = entry["path"]
        size = entry.get("size")
        if size is not None and size > max_bytes:
            logger.warning(
                "Skipping %s: %s bytes exceeds the %s byte limit for a collection file",
                path,
                size,
                max_bytes,
            )
            return None

        try:
            content = self._fetch_blob(client, path, max_bytes)
        except ResponseTooLarge:
            # The same condition as the check above, caught late because the listing's size
            # was wrong or absent. Skipping keeps the two paths to one verdict; raising would
            # abort the whole sync over a single file, which is what this loader avoids.
            logger.warning(
                "Skipping %s: it outgrew the %s byte limit for a collection file mid-download", path, max_bytes
            )
            return None

        # An empty file is ordinary in a repo -- a .gitkeep, an empty __init__.py -- but the
        # sync counts empty content as a per-file failure, which would leave the source
        # reporting a sync error on every run. There is nothing to index either way.
        if not content:
            logger.debug("Skipping %s: the file is empty", path)
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
        return SourceDocument(content=content, metadata=metadata)

    def _fetch_blob(self, client: httpx.Client, path: str, max_bytes: int) -> bytes:
        """Download a single file as the bytes GitHub stores.

        The raw media type returns the file itself rather than the JSON envelope, whose
        base64 ``content`` field is empty for anything over 1 MB.

        A transport or HTTP error is left to propagate and abort the sync. That is the
        conservative reading: a file absent from this run is treated as gone from the source
        and its already-synced copy deleted, so carrying on past a 500 or a rate limit would
        destroy good data over a transient error.
        """
        with client.stream(
            "GET",
            f"/repos/{self._repo}/contents/{quote(path)}",
            params={"ref": self.config.branch} if self.config.branch else None,
            headers={"Accept": "application/vnd.github.raw"},
        ) as response:
            response.raise_for_status()
            return read_capped(response, max_bytes, path)

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

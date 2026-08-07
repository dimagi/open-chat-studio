from unittest.mock import Mock

import httpx
import pytest

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.source_loaders.github import GitHubDocumentLoader

API_URL = "https://api.github.com"
TREE_URL = f"{API_URL}/repos/test/repo/git/trees/main?recursive=1"
CONTENTS_URL = f"{API_URL}/repos/test/repo/contents"


@pytest.fixture()
def github_config():
    return GitHubSourceConfig(
        repo_url="https://github.com/test/repo", branch="main", file_pattern="*.md", path_filter=""
    )


def _loader(config) -> GitHubDocumentLoader:
    return GitHubDocumentLoader(Mock(id=1), config, Mock(config={"token": "123"}))


def _blob(path: str, size: int = 10, sha: str | None = None) -> dict:
    return {"path": path, "type": "blob", "size": size, "sha": sha or f"sha-of-{path}"}


def _tree(httpx_mock, entries: list[dict], truncated: bool = False) -> None:
    """Register the repo listing. Each test registers the blob fetches it expects."""
    httpx_mock.add_response(url=TREE_URL, json={"tree": entries, "truncated": truncated})


class TestGitHubDocumentLoader:
    def test_extract_repo_info(self, github_config):
        owner, repo = github_config.extract_repo_info()
        assert owner == "test"
        assert repo == "repo"

    def test_matches_pattern(self, github_config):
        loader = _loader(github_config)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("docs/guide.md")
        assert not loader._matches_pattern("script.py")

    def test_matches_pattern_with_exclusions(self):
        config = GitHubSourceConfig(
            repo_url="https://github.com/test/repo",
            branch="main",
            file_pattern="!*_test.py, *.md, *.txt, *.py, !test.py",
        )
        loader = _loader(config)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("notes.txt")
        assert loader._matches_pattern("main.py")
        assert loader._matches_pattern("src/module.py")

        assert not loader._matches_pattern("test.py")
        assert not loader._matches_pattern("module_test.py")
        assert not loader._matches_pattern("tests/docs_test.py")
        assert not loader._matches_pattern("image.png")

    def test_load_documents(self, github_config, httpx_mock):
        paths = ["md_file.md", "docs/guide.md", "src/file2.md"]
        _tree(httpx_mock, [_blob(path) for path in paths])
        for i, path in enumerate(paths):
            httpx_mock.add_response(url=f"{CONTENTS_URL}/{path}?ref=main", content=f"test {i}".encode())

        documents = list(_loader(github_config).load_documents())

        assert [doc.content for doc in documents] == [b"test 0", b"test 1", b"test 2"]
        assert [doc.metadata["path"] for doc in documents] == paths
        assert all(doc.metadata["source_type"] == "github" for doc in documents)

    def test_identifier_is_the_blob_web_url(self, github_config, httpx_mock):
        """The identifier is what matches a document to an already-synced file, so a change
        here re-adds every file in the repo and deletes its predecessor."""
        _tree(httpx_mock, [_blob("docs/guide.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/guide.md?ref=main", content=b"body")

        loader = _loader(github_config)
        document = next(iter(loader.load_documents()))

        assert loader.get_document_identifier(document) == "https://github.com/test/repo/blob/main/docs/guide.md"

    def test_non_utf8_blob_is_yielded_as_its_own_bytes(self, github_config, httpx_mock):
        """The bug this replaces: the langchain loader decoded every blob as UTF-8, so one
        file that is not UTF-8 text raised UnicodeDecodeError out of the generator and aborted
        the whole sync -- taking the files after it down with it."""
        latin1 = "a stray byte: \xad and more".encode("latin-1")
        _tree(httpx_mock, [_blob("notes.md"), _blob("later.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/notes.md?ref=main", content=latin1)
        httpx_mock.add_response(url=f"{CONTENTS_URL}/later.md?ref=main", content=b"reached")

        documents = list(_loader(github_config).load_documents())

        assert [doc.content for doc in documents] == [latin1, b"reached"]

    def test_binary_blob_is_yielded_unparsed(self, github_config, httpx_mock):
        """Bytes as served, so the reader picks a parser at index time (ADR-0051)."""
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        config = GitHubSourceConfig(repo_url="https://github.com/test/repo", branch="main", file_pattern="*.png")
        _tree(httpx_mock, [_blob("logo.png")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/logo.png?ref=main", content=png)

        documents = list(_loader(config).load_documents())

        assert [doc.content for doc in documents] == [png]

    def test_directories_and_submodules_are_not_fetched(self, github_config, httpx_mock):
        """Only blobs have content. The tree also lists directories and submodule pointers,
        and fetching those spends a request per entry to be told there is nothing there."""
        _tree(
            httpx_mock,
            [
                {"path": "docs.md", "type": "tree", "sha": "tree-sha"},
                {"path": "vendor.md", "type": "commit", "sha": "commit-sha"},
                _blob("docs/guide.md"),
            ],
        )
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/guide.md?ref=main", content=b"body")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["docs/guide.md"]

    def test_oversized_blob_is_skipped_without_fetching(self, github_config, httpx_mock, settings):
        """The listing carries each blob's size, so an unstorable file costs no download."""
        settings.MAX_FILE_SIZE_MB = 1
        _tree(httpx_mock, [_blob("huge.md", size=1024 * 1024 + 1), _blob("small.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/small.md?ref=main", content=b"body")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["small.md"]

    def test_a_blob_that_outgrows_its_reported_size_is_skipped(self, github_config, httpx_mock, settings):
        """The size comes from the API, so the read is bounded on its own rather than trusting
        it to keep a whole repo out of the worker's memory. Reaching the cap mid-download is
        the same verdict as reading it in the listing -- skip this file, sync the rest."""
        settings.MAX_FILE_SIZE_MB = 1
        _tree(httpx_mock, [_blob("lying.md", size=10), _blob("small.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/lying.md?ref=main", content=b"x" * (1024 * 1024 + 1))
        httpx_mock.add_response(url=f"{CONTENTS_URL}/small.md?ref=main", content=b"body")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["small.md"]

    def test_empty_blob_is_skipped(self, github_config, httpx_mock):
        """An empty file is ordinary in a repo, but the sync counts empty content as a per-file
        failure -- which would leave the source reporting a sync error on every run."""
        _tree(httpx_mock, [_blob("placeholder.md", size=0), _blob("later.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/placeholder.md?ref=main", content=b"")
        httpx_mock.add_response(url=f"{CONTENTS_URL}/later.md?ref=main", content=b"reached")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["later.md"]

    def test_truncated_listing_aborts_before_any_fetch(self, github_config, httpx_mock):
        """The trees API drops entries past its limits and cannot paginate. A file missing from
        the listing is treated as deleted from the repo, so accepting a partial one would prune
        files that are still there -- failing leaves the collection untouched."""
        _tree(httpx_mock, [_blob("kept.md")], truncated=True)

        with pytest.raises(ValueError, match="truncated"):
            list(_loader(github_config).load_documents())

    def test_a_failed_blob_fetch_aborts_rather_than_pruning(self, github_config, httpx_mock):
        """The counterpart to skipping: a file this run does not yield is taken as gone from
        the source and its synced copy deleted. Carrying on past a 500 or a rate limit would
        destroy good data over a transient error, so the sync fails and retries instead."""
        _tree(httpx_mock, [_blob("broken.md"), _blob("later.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/broken.md?ref=main", status_code=500)

        with pytest.raises(httpx.HTTPStatusError):
            list(_loader(github_config).load_documents())

    def test_path_with_spaces_is_encoded(self, github_config, httpx_mock):
        _tree(httpx_mock, [_blob("docs/my notes.md")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/my%20notes.md?ref=main", content=b"body")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["docs/my notes.md"]

    def test_tree_listing_drives_blob_urls_and_headers(self, github_config, httpx_mock):
        """The listing request, the pattern filter applied to it, and the blob URL and auth
        headers derived from it, exercised together over a faked transport."""
        _tree(httpx_mock, [_blob("keep.md", size=4), _blob("skip.py", size=4)])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/keep.md?ref=main", content=b"body")

        documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["keep.md"]
        tree_request, blob_request = httpx_mock.get_requests()
        assert str(tree_request.url) == TREE_URL
        assert tree_request.headers["Authorization"] == "Bearer 123"
        assert blob_request.headers["Authorization"] == "Bearer 123"
        assert blob_request.headers["Accept"] == "application/vnd.github.raw"

    def test_sha_drives_the_update_check(self, github_config, httpx_mock):
        """A blob is re-downloaded on every sync but only rewritten when its sha moved."""
        _tree(httpx_mock, [_blob("notes.md", sha="new-sha")])
        httpx_mock.add_response(url=f"{CONTENTS_URL}/notes.md?ref=main", content=b"body")

        loader = _loader(github_config)
        document = next(iter(loader.load_documents()))

        assert loader.should_update_document(document, Mock(file=Mock(metadata={"sha": "new-sha"}))) is False
        assert loader.should_update_document(document, Mock(file=Mock(metadata={"sha": "old-sha"}))) is True

from unittest.mock import Mock, patch

import pytest

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.source_loaders.github import MAX_BLOB_BYTES, GitHubDocumentLoader

CONTENTS_URL = "https://api.github.com/repos/test/repo/contents"


@pytest.fixture()
def github_config():
    return GitHubSourceConfig(
        repo_url="https://github.com/test/repo", branch="main", file_pattern="*.md", path_filter=""
    )


def _loader(config) -> GitHubDocumentLoader:
    return GitHubDocumentLoader(Mock(id=1), config, Mock(config={"token": "123"}))


def _blob(path: str, size: int = 10, sha: str | None = None) -> dict:
    return {"path": path, "type": "blob", "size": size, "sha": sha or f"sha-of-{path}"}


def _with_tree(entries: list[dict]):
    """Stub the repo listing, leaving the per-blob fetches to httpx_mock."""
    return patch(
        "langchain_community.document_loaders.github.GithubFileLoader.get_file_paths",
        return_value=entries,
    )


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
        assert not loader._matches_pattern("README.txt")

    def test_matches_multiple_patterns_and_exclude(self):
        config = GitHubSourceConfig(
            repo_url="https://github.com/test/repo",
            branch="main",
            file_pattern="!*_test.py, *.md, *.txt, *.py, !test.py",
        )
        loader = _loader(config)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("notes.txt")
        assert loader._matches_pattern("docs/index.md")
        assert loader._matches_pattern("script.py")
        assert loader._matches_pattern("src/test.py")  # not matched because of the subdirectory
        assert not loader._matches_pattern("test.py")
        assert not loader._matches_pattern("hello_test.py")
        assert not loader._matches_pattern("tests/docs_test.py")
        assert not loader._matches_pattern("image.png")

    def test_load_documents(self, github_config, httpx_mock):
        paths = ["md_file.md", "docs/guide.md", "src/file2.md"]
        for i, path in enumerate(paths):
            httpx_mock.add_response(url=f"{CONTENTS_URL}/{path}?ref=main", content=f"test {i}".encode())

        with _with_tree([_blob(path) for path in paths]):
            documents = list(_loader(github_config).load_documents())

        assert [doc.content for doc in documents] == [b"test 0", b"test 1", b"test 2"]
        assert [doc.metadata["path"] for doc in documents] == paths
        assert all(doc.metadata["source_type"] == "github" for doc in documents)

    def test_identifier_is_the_blob_web_url(self, github_config, httpx_mock):
        """The identifier is what matches a document to an already-synced file, so a change
        here re-adds every file in the repo and deletes its predecessor."""
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/guide.md?ref=main", content=b"body")

        with _with_tree([_blob("docs/guide.md")]):
            loader = _loader(github_config)
            document = next(iter(loader.load_documents()))

        assert loader.get_document_identifier(document) == "https://github.com/test/repo/blob/main/docs/guide.md"

    def test_non_utf8_blob_is_yielded_as_its_own_bytes(self, github_config, httpx_mock):
        """The bug this replaces: langchain's loader decodes every blob as UTF-8, so one file
        that is not UTF-8 text raised UnicodeDecodeError out of the generator and aborted the
        whole sync -- taking the files after it down with it."""
        latin1 = "a stray byte: \xad and more".encode("latin-1")
        httpx_mock.add_response(url=f"{CONTENTS_URL}/notes.md?ref=main", content=latin1)
        httpx_mock.add_response(url=f"{CONTENTS_URL}/later.md?ref=main", content=b"reached")

        with _with_tree([_blob("notes.md"), _blob("later.md")]):
            documents = list(_loader(github_config).load_documents())

        assert [doc.content for doc in documents] == [latin1, b"reached"]

    def test_binary_blob_is_yielded_unparsed(self, github_config, httpx_mock):
        """Bytes as served, so the reader picks a parser at index time (ADR-0051)."""
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        config = GitHubSourceConfig(repo_url="https://github.com/test/repo", branch="main", file_pattern="*.png")
        httpx_mock.add_response(url=f"{CONTENTS_URL}/logo.png?ref=main", content=png)

        with _with_tree([_blob("logo.png")]):
            documents = list(_loader(config).load_documents())

        assert [doc.content for doc in documents] == [png]

    def test_directories_and_submodules_are_not_fetched(self, github_config, httpx_mock):
        """Only blobs have content. The tree also lists directories and submodule pointers,
        and fetching those spends a request per entry to be told there is nothing there."""
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/guide.md?ref=main", content=b"body")
        entries = [
            {"path": "docs.md", "type": "tree", "sha": "tree-sha"},
            {"path": "vendor.md", "type": "commit", "sha": "commit-sha"},
            _blob("docs/guide.md"),
        ]

        with _with_tree(entries):
            documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["docs/guide.md"]

    def test_oversized_blob_is_skipped_without_fetching(self, github_config, httpx_mock):
        """The listing carries each blob's size, so an unstorable file costs no download."""
        httpx_mock.add_response(url=f"{CONTENTS_URL}/small.md?ref=main", content=b"body")

        with _with_tree([_blob("huge.md", size=MAX_BLOB_BYTES + 1), _blob("small.md")]):
            documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["small.md"]

    def test_a_blob_that_outgrows_its_reported_size_is_capped(self, github_config, httpx_mock):
        """The size comes from the API, so the read is bounded on its own rather than
        trusting it to keep a whole repo out of the worker's memory."""
        httpx_mock.add_response(url=f"{CONTENTS_URL}/lying.md?ref=main", content=b"x" * (MAX_BLOB_BYTES + 1))

        with _with_tree([_blob("lying.md", size=10)]):
            with pytest.raises(ValueError, match="exceeds"):
                list(_loader(github_config).load_documents())

    def test_path_with_spaces_is_encoded(self, github_config, httpx_mock):
        httpx_mock.add_response(url=f"{CONTENTS_URL}/docs/my%20notes.md?ref=main", content=b"body")

        with _with_tree([_blob("docs/my notes.md")]):
            documents = list(_loader(github_config).load_documents())

        assert [doc.metadata["path"] for doc in documents] == ["docs/my notes.md"]

    def test_whole_path_with_only_the_sockets_faked(self, github_config, httpx_mock):
        """The other tests stub the listing, so nothing else exercises the real tree request
        together with the blob URL and auth headers built from it."""
        tree = {
            "tree": [
                {"path": "keep.md", "type": "blob", "size": 4, "sha": "sha-1"},
                {"path": "skip.py", "type": "blob", "size": 4, "sha": "sha-2"},
            ]
        }
        tree_response = Mock(raise_for_status=Mock(), json=Mock(return_value=tree))
        httpx_mock.add_response(url=f"{CONTENTS_URL}/keep.md?ref=main", content=b"body")

        with patch("langchain_community.document_loaders.github.requests.get", return_value=tree_response) as get:
            documents = list(_loader(github_config).load_documents())

        assert get.call_args.args[0] == "https://api.github.com/repos/test/repo/git/trees/main?recursive=1"
        assert [doc.metadata["path"] for doc in documents] == ["keep.md"]
        blob_request = httpx_mock.get_requests()[0]
        assert blob_request.headers["Authorization"] == "Bearer 123"
        assert blob_request.headers["Accept"] == "application/vnd.github.raw"

    def test_sha_drives_the_update_check(self, github_config, httpx_mock):
        """A blob is re-downloaded on every sync but only rewritten when its sha moved."""
        httpx_mock.add_response(url=f"{CONTENTS_URL}/notes.md?ref=main", content=b"body")

        with _with_tree([_blob("notes.md", sha="new-sha")]):
            loader = _loader(github_config)
            document = next(iter(loader.load_documents()))

        assert loader.should_update_document(document, Mock(file=Mock(metadata={"sha": "new-sha"}))) is False
        assert loader.should_update_document(document, Mock(file=Mock(metadata={"sha": "old-sha"}))) is True

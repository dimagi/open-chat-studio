import hashlib
from collections.abc import Iterator
from unittest.mock import Mock, patch

import pytest
from langchain_core.documents import Document

from apps.documents.datamodels import GitHubSourceConfig
from apps.documents.source_loaders.github import GitHubDocumentLoader


@pytest.fixture()
def github_config():
    return GitHubSourceConfig(
        repo_url="https://github.com/test/repo",  # ty: ignore[invalid-argument-type]
        branch="main",
        file_pattern="*.md",
        path_filter="",
    )


class TestGitHubDocumentLoader:
    def test_extract_repo_info(self, github_config):
        owner, repo = github_config.extract_repo_info()
        assert owner == "test"
        assert repo == "repo"

    def test_matches_pattern(self, github_config):
        loader = GitHubDocumentLoader(Mock(), github_config, None)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("docs/guide.md")
        assert not loader._matches_pattern("script.py")
        assert not loader._matches_pattern("README.txt")

    def test_matches_multiple_patterns_and_exclude(self):
        config = GitHubSourceConfig(
            repo_url="https://github.com/test/repo",  # ty: ignore[invalid-argument-type]
            branch="main",
            file_pattern="!*_test.py, *.md, *.txt, *.py, !test.py",
        )
        loader = GitHubDocumentLoader(Mock(), config, None)

        assert loader._matches_pattern("README.md")
        assert loader._matches_pattern("notes.txt")
        assert loader._matches_pattern("docs/index.md")
        assert loader._matches_pattern("script.py")
        assert loader._matches_pattern("src/test.py")  # not matched because of the subdirectory
        assert not loader._matches_pattern("test.py")
        assert not loader._matches_pattern("hello_test.py")
        assert not loader._matches_pattern("tests/docs_test.py")
        assert not loader._matches_pattern("image.png")

    def test_load_documents(self, github_config):
        loader = GitHubDocumentLoader(Mock(), github_config, Mock(config={"token": "123"}))
        with patch("langchain_community.document_loaders.github.GithubFileLoader.lazy_load") as lazy_load:
            lazy_load.return_value = _get_mock_document_iterator()
            documents = list(loader.load_documents())

        expected_raw_docs = _get_mock_documents()
        assert [doc.page_content for doc in documents] == [doc.page_content for doc in expected_raw_docs]
        assert all("source_type" in doc.metadata for doc in documents)


def _get_mock_document_iterator() -> Iterator[Document]:
    yield from _get_mock_documents()


def _get_mock_documents(paths: list[str] | None = None) -> list[Document]:
    paths = paths or ["md_file.md", "txt_file.txt", "py_file.py", "src/file1.py", "src/file2.md", "src/file3.txt"]
    metadata = [
        {
            "path": path,
            "sha": hashlib.sha1(path.encode()),
            "source": f"source for {path}",
        }
        for path in paths
    ]
    return [Document(page_content=f"test {i}", metadata=meta) for i, meta in enumerate(metadata)]

import pydantic
from pydantic import HttpUrl, field_validator


class ChunkingStrategy(pydantic.BaseModel):
    chunk_size: int = pydantic.Field(description="Size of each chunk in tokens")
    chunk_overlap: int = pydantic.Field(description="Number of overlapping tokens between chunks")


class CollectionFileMetadata(pydantic.BaseModel):
    chunking_strategy: ChunkingStrategy = pydantic.Field(description="Chunking strategy used for the file")


class GitHubSourceConfig(pydantic.BaseModel):
    repo_url: HttpUrl = pydantic.Field(description="GitHub repository URL")
    branch: str = pydantic.Field(default="main", description="Branch to sync from")
    file_pattern: str = pydantic.Field(
        default="*.md", description="File pattern to match (e.g., *.md, src/*.py, !test_*)"
    )
    path_filter: str = pydantic.Field(default="", description="Optional path prefix filter")

    def __str__(self):
        owner, repo = self.extract_repo_info()
        return f"{owner}/{repo}"

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value):
        if value.host != "github.com" or value.scheme != "https":
            raise ValueError(f"'{value}' is not a valid GitHub repository URL'")
        GitHubSourceConfig._extract_repo_info(value)
        return value

    def extract_repo_info(self) -> tuple[str, str]:
        """Extract owner and repo name from GitHub URL"""
        return GitHubSourceConfig._extract_repo_info(self.repo_url)

    @staticmethod
    def _extract_repo_info(repo_url: HttpUrl) -> tuple[str, str]:
        path = repo_url.path.lstrip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        else:
            raise ValueError(f"Unable to extract owner/repo from URL: {repo_url}")


class ConfluenceSourceConfig(pydantic.BaseModel):
    base_url: str = pydantic.Field(description="Confluence base URL")
    space_key: str = pydantic.Field(description="Confluence space key")

    def __str__(self):
        return f"{self.base_url}/spaces/{self.space_key}/"


class DocumentSourceConfig(pydantic.BaseModel):
    github: GitHubSourceConfig | None = pydantic.Field(default=None, description="GitHub source configuration")
    confluence: ConfluenceSourceConfig | None = pydantic.Field(
        default=None, description="Confluence source configuration"
    )

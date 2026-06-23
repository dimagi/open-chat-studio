from typing import Any, Literal

import pydantic
from pydantic import HttpUrl, field_validator

# Audio/video types markitdown cannot extract useful text from.
DEFAULT_UNSUPPORTED_FILE_TYPES = ["mp3", "mp4", "wav", "ogg", "avi", "mov", "mkv"]


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

    # Loading options - only one should be specified
    space_key: str = pydantic.Field(default="", description="Confluence space key")
    label: str = pydantic.Field(default="", description="Confluence label to filter pages")
    cql: str = pydantic.Field(default="", description="Confluence Query Language (CQL) query")
    page_ids: str = pydantic.Field(default="", description="Comma-separated list of page IDs")

    # Additional options
    max_pages: int = pydantic.Field(default=1000, description="Maximum number of pages to load")

    @pydantic.model_validator(mode="after")
    def validate_loading_options(self):
        options = [self.space_key, self.label, self.cql, self.page_ids]
        non_empty_options = [opt for opt in options if opt.strip()]

        if len(non_empty_options) == 0:
            raise ValueError("At least one loading option must be specified: space_key, label, cql, or page_ids")
        if len(non_empty_options) > 1:
            raise ValueError("Only one loading option can be specified at a time")

        return self

    def get_loader_kwargs(self) -> dict[str, Any]:
        """Get the appropriate kwargs for ConfluenceLoader based on the specified option"""
        kwargs: dict[str, Any] = {
            "url": self.base_url,
            "max_pages": self.max_pages,
        }

        if self.space_key.strip():
            kwargs["space_key"] = self.space_key.strip()
        elif self.label.strip():
            kwargs["label"] = self.label.strip()
        elif self.cql.strip():
            kwargs["cql"] = self.cql.strip()
        elif self.page_ids.strip():
            # Convert comma-separated string to list of integers
            try:
                page_id_list = [int(pid.strip()) for pid in self.page_ids.split(",") if pid.strip()]
                kwargs["page_ids"] = page_id_list
            except ValueError:
                raise ValueError("Page IDs must be comma-separated integers") from None

        return kwargs

    def __str__(self):
        if self.space_key.strip():
            return f"{self.base_url}/spaces/{self.space_key}/"
        elif self.label.strip():
            return f"{self.base_url} (label: {self.label})"
        elif self.cql.strip():
            return f"{self.base_url} (CQL: {self.cql[:50]}...)"
        elif self.page_ids.strip():
            return f"{self.base_url} (pages: {self.page_ids})"
        return self.base_url


class MetadataFilter(pydantic.BaseModel):
    """A single condition evaluated against a feed item's raw metadata."""

    field: str = pydantic.Field(description="Name of the item metadata field to test")
    operator: Literal["eq", "in"] = pydantic.Field(default="eq", description="Comparison operator")
    value: Any = pydantic.Field(default=None, description="Value to compare the field against")

    def matches(self, item: dict[str, Any]) -> bool:
        """Return True if `item` satisfies this filter."""
        item_value = item.get(self.field)
        if self.operator == "eq":
            return item_value == self.value
        # "in": item matches if the item's value (or any element of a list-valued field)
        # is one of the allowed values.
        allowed = self.value if isinstance(self.value, list) else [self.value]
        if isinstance(item_value, list):
            return any(element in allowed for element in item_value)
        return item_value in allowed


class JSONCollectionSourceConfig(pydantic.BaseModel):
    json_url: HttpUrl = pydantic.Field(description="URL of the JSON feed")
    request_timeout: int = pydantic.Field(
        default=30,
        ge=5,
        le=300,
        description="HTTP request timeout in seconds, applied to JSON fetch and each attachment download",
    )
    metadata_filters: list[MetadataFilter] = pydantic.Field(
        default_factory=list,
        description="Only items satisfying all of these conditions are indexed",
    )
    unsupported_file_types: list[str] = pydantic.Field(
        default_factory=lambda: list(DEFAULT_UNSUPPORTED_FILE_TYPES),
        description="Attachment file types to skip without downloading (case-insensitive)",
    )

    def is_unsupported_file_type(self, file_type: str | None) -> bool:
        if not file_type:
            return False
        return file_type.lower() in {t.lower() for t in self.unsupported_file_types}

    def __str__(self) -> str:
        return str(self.json_url)


class DocumentSourceConfig(pydantic.BaseModel):
    github: GitHubSourceConfig | None = pydantic.Field(default=None, description="GitHub source configuration")
    confluence: ConfluenceSourceConfig | None = pydantic.Field(
        default=None, description="Confluence source configuration"
    )
    json_collection: JSONCollectionSourceConfig | None = pydantic.Field(
        default=None, description="JSON collection source configuration"
    )

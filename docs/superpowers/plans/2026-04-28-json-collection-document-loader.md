# JSON Collection Document Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `JSONCollectionLoader` that fetches a JSON "indexed-collections" feed, follows attachment links to extract PDF/document text, and creates `CollectionFile`s — slotting into the existing collections framework alongside GitHub and Confluence loaders. UI is gated behind a Waffle feature flag.

**Architecture:** A new `SourceType.JSON_COLLECTION` enum value, a pydantic config (`JSONCollectionSourceConfig`), a `JSONCollectionLoader` subclass of `BaseDocumentLoader` that uses `httpx` for both the JSON fetch and per-attachment fetches, the existing `markitdown_read` reader for text extraction, a Django form, view + template wiring, and a Waffle flag (`flag_json_collection_loader`) that filters the source-type picker and 404s direct creation requests when off.

**Tech Stack:** Django 5.x, pydantic v2, `httpx` (already in deps), `pytest-httpx` (`httpx_mock` fixture, already in deps), `markitdown` (already used by `apps/documents/readers.py`), Waffle flags.

**Spec:** [`docs/superpowers/specs/2026-04-28-json-collection-document-loader-design.md`](../specs/2026-04-28-json-collection-document-loader-design.md)

---

## File Structure

| File | Purpose | Status |
|---|---|---|
| `apps/teams/flags.py` | Add `JSON_COLLECTION_LOADER` flag definition | Modify |
| `apps/documents/datamodels.py` | Add `JSONCollectionSourceConfig`; extend `DocumentSourceConfig` | Modify |
| `apps/documents/models.py` | Add `SourceType.JSON_COLLECTION`, css_logo entry, `source_config` branch | Modify |
| `apps/documents/migrations/00XX_alter_documentsource_source_type.py` | Generated `AlterField` migration | Create (via `makemigrations`) |
| `apps/documents/source_loaders/json_collection.py` | `JSONCollectionLoader` class | Create |
| `apps/documents/source_loaders/registry.py` | Register new loader in `LOADERS` | Modify |
| `apps/documents/forms.py` | `JSONCollectionDocumentSourceForm` | Modify |
| `apps/documents/views.py` | Form-class map entry + flag-gate dispatch + filter `document_source_types` | Modify |
| `apps/documents/tests/data/indexed_collection_full.json` | Fixture: items with attachments + optional fields | Create |
| `apps/documents/tests/data/indexed_collection_minimal.json` | Fixture: items with only `title`/`URI` | Create |
| `apps/documents/tests/data/indexed_collection_mixed.json` | Fixture: mixed and malformed items | Create |
| `apps/documents/tests/test_json_collection_loader.py` | Unit tests for the loader | Create |
| `apps/documents/tests/test_views.py` | Add view tests for JSON-collection creation under flag on/off | Modify |

No template change is required for this iteration: the picker iterates `document_source_types` from the view context, so filtering that list in the view is the cleanest UI gate (no `{% flag %}` block needed in the template).

---

## Task 1: Register the feature flag

**Files:**
- Modify: `apps/teams/flags.py`

- [ ] **Step 1: Add the flag entry**

In `apps/teams/flags.py`, add a new entry to the `Flags` enum, alongside the existing entries (e.g. after `EMAIL_CHANNEL`). Match the 5-tuple form used by other flags:

```python
    JSON_COLLECTION_LOADER = (
        "flag_json_collection_loader",
        "JSON Collection document source loader",
        "",
        [],
        True,
    )
```

- [ ] **Step 2: Verify the flag is reachable**

Run:

```bash
uv run python -c "from apps.teams.flags import Flags; print(Flags.JSON_COLLECTION_LOADER.slug)"
```

Expected output: `flag_json_collection_loader`

- [ ] **Step 3: Commit**

```bash
git add apps/teams/flags.py
git commit -m "feat: add flag_json_collection_loader feature flag"
```

---

## Task 2: Add `SourceType.JSON_COLLECTION` and migration

**Files:**
- Modify: `apps/documents/models.py`
- Create: `apps/documents/migrations/00XX_alter_documentsource_source_type.py` (generated)

- [ ] **Step 1: Extend the `SourceType` choices**

In `apps/documents/models.py`, edit the `SourceType` enum (around line 354) to add the new value, and add a CSS-logo entry:

```python
class SourceType(models.TextChoices):
    GITHUB = "github", _("GitHub Repository")
    CONFLUENCE = "confluence", _("Confluence")
    JSON_COLLECTION = "json_collection", _("JSON Collection")

    @property
    def css_logo(self):
        return {
            SourceType.GITHUB: "fa-brands fa-github",
            SourceType.CONFLUENCE: "fa-brands fa-confluence",
            SourceType.JSON_COLLECTION: "fa-solid fa-file-code",
        }[self]
```

- [ ] **Step 2: Extend `DocumentSource.source_config`**

In the same file, edit the `source_config` property on `DocumentSource` (around line 419) to handle the new type:

```python
    @property
    def source_config(self):
        """Get the configuration for the specific source type"""
        if self.source_type == SourceType.GITHUB:
            return self.config.github
        elif self.source_type == SourceType.CONFLUENCE:
            return self.config.confluence
        elif self.source_type == SourceType.JSON_COLLECTION:
            return self.config.json_collection
        return None
```

> Note: `JSONCollectionSourceConfig` and the `json_collection` attribute on `DocumentSourceConfig` are added in Task 3. This step references them, so the import will resolve once Task 3 is complete. If type-checking complains in this intermediate state, do Tasks 2 and 3 back-to-back without committing in between (commit at the end of Task 3).

- [ ] **Step 3: Generate the migration**

```bash
uv run python manage.py makemigrations documents
```

Expected: a new file `apps/documents/migrations/00XX_alter_documentsource_source_type.py` is created with an `AlterField` operation on `DocumentSource.source_type` updating the `choices` list.

- [ ] **Step 4: Apply the migration locally**

```bash
uv run python manage.py migrate documents
```

Expected: the migration runs without error.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/documents/models.py --fix && uv run ruff format apps/documents/models.py
```

Expected: no errors.

- [ ] **Step 6: Defer commit until Task 3 is done**

Don't commit yet — Task 3 finishes the data model change.

---

## Task 3: Add `JSONCollectionSourceConfig` and extend `DocumentSourceConfig`

**Files:**
- Modify: `apps/documents/datamodels.py`
- Test: `apps/documents/tests/test_models.py` (add test class)

- [ ] **Step 1: Write the failing tests**

Append to `apps/documents/tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from apps.documents.datamodels import DocumentSourceConfig, JSONCollectionSourceConfig


class TestJSONCollectionSourceConfig:
    def test_valid_config(self):
        config = JSONCollectionSourceConfig(json_url="https://example.com/feed.json")
        assert str(config.json_url) == "https://example.com/feed.json"
        assert config.request_timeout == 30

    def test_str_representation(self):
        config = JSONCollectionSourceConfig(json_url="https://example.com/feed.json")
        assert str(config) == "https://example.com/feed.json"

    def test_invalid_url(self):
        with pytest.raises(ValidationError):
            JSONCollectionSourceConfig(json_url="not-a-url")

    def test_request_timeout_bounds(self):
        # too small
        with pytest.raises(ValidationError):
            JSONCollectionSourceConfig(json_url="https://example.com/x", request_timeout=1)
        # too large
        with pytest.raises(ValidationError):
            JSONCollectionSourceConfig(json_url="https://example.com/x", request_timeout=999)
        # acceptable
        cfg = JSONCollectionSourceConfig(json_url="https://example.com/x", request_timeout=60)
        assert cfg.request_timeout == 60

    def test_document_source_config_accepts_json_collection(self):
        wrapper = DocumentSourceConfig(
            json_collection=JSONCollectionSourceConfig(json_url="https://example.com/feed.json"),
        )
        assert wrapper.json_collection is not None
        assert wrapper.github is None
        assert wrapper.confluence is None
```

(If `apps/documents/tests/test_models.py` does not yet exist or does not have a top-level imports area for `pytest`/`ValidationError`, add those at the top of the file rather than appending duplicates.)

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_models.py::TestJSONCollectionSourceConfig -v
```

Expected: `ImportError` or `AttributeError` for `JSONCollectionSourceConfig` — confirms the model isn't yet defined.

- [ ] **Step 3: Add `JSONCollectionSourceConfig` and extend `DocumentSourceConfig`**

In `apps/documents/datamodels.py`:

a) Add the new model below `ConfluenceSourceConfig`:

```python
class JSONCollectionSourceConfig(pydantic.BaseModel):
    json_url: HttpUrl = pydantic.Field(description="URL of the JSON feed")
    request_timeout: int = pydantic.Field(
        default=30,
        ge=5,
        le=300,
        description="HTTP request timeout in seconds, applied to JSON fetch and each attachment download",
    )

    def __str__(self) -> str:
        return str(self.json_url)
```

b) Extend `DocumentSourceConfig`:

```python
class DocumentSourceConfig(pydantic.BaseModel):
    github: GitHubSourceConfig | None = pydantic.Field(default=None, description="GitHub source configuration")
    confluence: ConfluenceSourceConfig | None = pydantic.Field(
        default=None, description="Confluence source configuration"
    )
    json_collection: JSONCollectionSourceConfig | None = pydantic.Field(
        default=None, description="JSON collection source configuration"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_models.py::TestJSONCollectionSourceConfig -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/documents/datamodels.py apps/documents/tests/test_models.py --fix
uv run ruff format apps/documents/datamodels.py apps/documents/tests/test_models.py apps/documents/models.py
```

- [ ] **Step 6: Commit Tasks 2 + 3 together**

```bash
git add apps/documents/models.py apps/documents/datamodels.py apps/documents/migrations/ apps/documents/tests/test_models.py
git commit -m "feat: add JSON_COLLECTION source type and config schema"
```

---

## Task 4: Loader scaffold and registry entry

**Files:**
- Create: `apps/documents/source_loaders/json_collection.py`
- Modify: `apps/documents/source_loaders/registry.py`
- Test: `apps/documents/tests/test_json_collection_loader.py`

- [ ] **Step 1: Write the failing test**

Create `apps/documents/tests/test_json_collection_loader.py`:

```python
from unittest.mock import Mock

import pytest

from apps.documents.datamodels import (
    DocumentSourceConfig,
    JSONCollectionSourceConfig,
)
from apps.documents.models import SourceType
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.documents.source_loaders.registry import LOADERS


@pytest.fixture()
def json_config():
    return JSONCollectionSourceConfig(json_url="https://example.com/feed.json")


class TestJSONCollectionLoaderConstruction:
    def test_for_document_source_constructs_loader_without_auth(self, json_config):
        collection = Mock()
        document_source = Mock()
        document_source.config = DocumentSourceConfig(json_collection=json_config)
        document_source.auth_provider = None

        loader = JSONCollectionLoader.for_document_source(collection, document_source)

        assert isinstance(loader, JSONCollectionLoader)
        assert loader.collection is collection
        assert loader.config is json_config
        assert loader.auth_provider is None

    def test_loader_registered(self):
        assert LOADERS[SourceType.JSON_COLLECTION] is JSONCollectionLoader
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py -v
```

Expected: `ImportError` for `JSONCollectionLoader`.

- [ ] **Step 3: Create the loader scaffold**

Create `apps/documents/source_loaders/json_collection.py`:

```python
import logging
from collections.abc import Iterator
from typing import Self

from langchain_core.documents import Document

from apps.documents.datamodels import JSONCollectionSourceConfig
from apps.documents.models import Collection, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader

logger = logging.getLogger(__name__)


class JSONCollectionLoader(BaseDocumentLoader[JSONCollectionSourceConfig]):
    """Document loader for JSON 'indexed collections' feeds."""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        return cls(collection, document_source.config.json_collection, auth_provider=None)

    def load_documents(self) -> Iterator[Document]:
        raise NotImplementedError  # implemented in later tasks
        yield  # pragma: no cover  -- keeps function an Iterator
```

- [ ] **Step 4: Register the loader**

Edit `apps/documents/source_loaders/registry.py`:

```python
from apps.documents.models import SourceType
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.documents.source_loaders.confluence import ConfluenceDocumentLoader
from apps.documents.source_loaders.github import GitHubDocumentLoader
from apps.documents.source_loaders.json_collection import JSONCollectionLoader

LOADERS = {
    SourceType.GITHUB: GitHubDocumentLoader,
    SourceType.CONFLUENCE: ConfluenceDocumentLoader,
    SourceType.JSON_COLLECTION: JSONCollectionLoader,
}
```

(Keep the existing `create_loader` function below unchanged.)

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py -v
```

Expected: 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/documents/source_loaders/json_collection.py apps/documents/source_loaders/registry.py apps/documents/tests/test_json_collection_loader.py
git commit -m "feat: scaffold JSONCollectionLoader and register in loader registry"
```

---

## Task 5: `load_documents` — JSON fetch and root-list validation

**Files:**
- Modify: `apps/documents/source_loaders/json_collection.py`
- Test: `apps/documents/tests/test_json_collection_loader.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/documents/tests/test_json_collection_loader.py`:

```python
import httpx
from unittest.mock import Mock

# (json_config fixture from earlier remains in scope)


def _make_loader(json_config, collection_id=42):
    collection = Mock()
    collection.id = collection_id
    return JSONCollectionLoader(collection=collection, config=json_config, auth_provider=None)


class TestLoadDocumentsRoot:
    def test_http_error_raises(self, json_config, httpx_mock):
        httpx_mock.add_response(
            url="https://example.com/feed.json", status_code=500, json={}
        )
        loader = _make_loader(json_config)
        with pytest.raises(httpx.HTTPStatusError):
            list(loader.load_documents())

    def test_non_list_root_raises(self, json_config, httpx_mock):
        httpx_mock.add_response(
            url="https://example.com/feed.json", json={"results": []}
        )
        loader = _make_loader(json_config)
        with pytest.raises(ValueError, match="expected a JSON list"):
            list(loader.load_documents())

    def test_empty_list_yields_nothing(self, json_config, httpx_mock):
        httpx_mock.add_response(url="https://example.com/feed.json", json=[])
        loader = _make_loader(json_config)
        assert list(loader.load_documents()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsRoot -v
```

Expected: failures with `NotImplementedError` (current loader stub).

- [ ] **Step 3: Implement `load_documents` root-level behaviour**

Replace the loader file body in `apps/documents/source_loaders/json_collection.py` with the following — note this is a partial implementation that handles only the root validation; per-item processing comes in later tasks but is stubbed here so the iteration shape is correct:

```python
import logging
from collections.abc import Iterator
from typing import Any, Self

import httpx
from langchain_core.documents import Document

from apps.documents.datamodels import JSONCollectionSourceConfig
from apps.documents.models import Collection, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader

logger = logging.getLogger(__name__)


class JSONCollectionLoader(BaseDocumentLoader[JSONCollectionSourceConfig]):
    """Document loader for JSON 'indexed collections' feeds."""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        return cls(collection, document_source.config.json_collection, auth_provider=None)

    def load_documents(self) -> Iterator[Document]:
        items = self._fetch_json_list()
        for item in items:
            yield from self._process_item(item)

    def _fetch_json_list(self) -> list[dict[str, Any]]:
        response = httpx.get(
            str(self.config.json_url),
            timeout=self.config.request_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list at the top level, got {type(data).__name__}")
        return data

    def _process_item(self, item: dict[str, Any]) -> Iterator[Document]:
        return iter(())  # filled in by Task 6+
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsRoot -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/documents/source_loaders/json_collection.py apps/documents/tests/test_json_collection_loader.py
git commit -m "feat: JSONCollectionLoader fetches and validates JSON root"
```

---

## Task 6: `_process_item` — multi-attachment item produces N Documents

**Files:**
- Modify: `apps/documents/source_loaders/json_collection.py`
- Test: `apps/documents/tests/test_json_collection_loader.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/documents/tests/test_json_collection_loader.py`:

```python
class TestLoadDocumentsAttachments:
    def test_multi_attachment_yields_one_doc_per_attachment(self, json_config, httpx_mock):
        feed = [
            {
                "title": "Doc A",
                "URI": "https://example.com/a",
                "date": "08/04/2025",
                "type": "Meeting reports",
                "languages": ["en"],
                "attachments": [
                    {
                        "file_type": "pdf",
                        "file_size": "100KB",
                        "title": "First PDF",
                        "link": "https://example.com/a-1.pdf",
                    },
                    {
                        "file_type": "pdf",
                        "file_size": "200KB",
                        "title": "Second PDF",
                        "link": "https://example.com/a-2.pdf",
                    },
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(
            url="https://example.com/a-1.pdf",
            content=b"%PDF-content-1",
            headers={"content-type": "application/pdf"},
        )
        httpx_mock.add_response(
            url="https://example.com/a-2.pdf",
            content=b"%PDF-content-2",
            headers={"content-type": "application/pdf"},
        )

        loader = _make_loader(json_config, collection_id=42)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read"
        ) as md:
            md.side_effect = [
                _stub_doc("text from pdf 1"),
                _stub_doc("text from pdf 2"),
            ]
            docs = list(loader.load_documents())

        assert len(docs) == 2
        assert docs[0].page_content == "text from pdf 1"
        assert docs[0].metadata["source"] == "https://example.com/a-1.pdf"
        assert docs[0].metadata["link"] == "https://example.com/a-1.pdf"
        assert docs[0].metadata["file_type"] == "pdf"
        assert docs[0].metadata["attachment_title"] == "First PDF"
        # item-level metadata is propagated:
        assert docs[0].metadata["title"] == "Doc A"
        assert docs[0].metadata["URI"] == "https://example.com/a"
        assert docs[0].metadata["date"] == "08/04/2025"
        assert docs[0].metadata["languages"] == ["en"]
        # framework-required:
        assert docs[0].metadata["collection_id"] == 42
        assert docs[0].metadata["source_type"] == "json_collection"
        assert docs[0].metadata["citation_text"] == "Doc A"
        assert docs[0].metadata["citation_url"] == "https://example.com/a"

        assert docs[1].page_content == "text from pdf 2"
        assert docs[1].metadata["source"] == "https://example.com/a-2.pdf"
```

Add the helper imports at the top of the test file (if not already present):

```python
from unittest import mock

from apps.documents.readers import Document as ReaderDocument, DocumentPart


def _stub_doc(text: str) -> ReaderDocument:
    return ReaderDocument(parts=[DocumentPart(content=text)])
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsAttachments -v
```

Expected: failure — `_process_item` returns nothing.

- [ ] **Step 3: Implement `_process_item` for the attachment path**

In `apps/documents/source_loaders/json_collection.py`:

a) Add new imports near the top (preserving existing ones):

```python
from io import BytesIO

from apps.documents.readers import markitdown_read
```

b) Replace `_process_item` and add helpers:

```python
    def _process_item(self, item: dict[str, Any]) -> Iterator[Document]:
        title = item.get("title")
        uri = item.get("URI")
        if not title and not uri:
            logger.warning(
                "Skipping item with neither 'title' nor 'URI': %r", item,
            )
            return

        item_metadata = self._build_item_metadata(item, title=title, uri=uri)
        fetchable = [a for a in (item.get("attachments") or []) if a.get("link")]

        if fetchable:
            yield from self._yield_attachment_documents(item_metadata, fetchable, uri)
            return

        # Fallback: no fetchable attachments
        if title:
            yield Document(
                page_content=title,
                metadata={**item_metadata, "source": uri or ""},
            )
        else:
            logger.warning(
                "Skipping item with no fetchable attachments and no title (URI=%s)", uri,
            )

    def _build_item_metadata(
        self, item: dict[str, Any], *, title: str | None, uri: str | None
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "collection_id": self.collection.id,
            "source_type": "json_collection",
        }
        if title is not None:
            metadata["title"] = title
            metadata["citation_text"] = title
        if uri is not None:
            metadata["URI"] = uri
            metadata["citation_url"] = uri
        for key in ("date", "type", "languages", "authors", "publisher",
                    "countries", "diseases", "tags", "regions"):
            if key in item:
                metadata[key] = item[key]
        return metadata

    def _yield_attachment_documents(
        self,
        item_metadata: dict[str, Any],
        fetchable: list[dict[str, Any]],
        item_uri: str | None,
    ) -> Iterator[Document]:
        for attachment in fetchable:
            link = attachment["link"]
            try:
                text = self._fetch_and_extract(link)
            except Exception as exc:  # noqa: BLE001 -- caught and logged per design
                logger.warning(
                    "Skipping attachment %s for item %s: %s", link, item_uri, exc,
                )
                continue
            metadata = {
                **item_metadata,
                "file_type": attachment.get("file_type"),
                "file_size": attachment.get("file_size"),
                "attachment_title": attachment.get("title"),
                "link": link,
                "source": link,
            }
            yield Document(page_content=text, metadata=metadata)

    def _fetch_and_extract(self, url: str) -> str:
        response = httpx.get(
            url,
            timeout=self.config.request_timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        doc = markitdown_read(BytesIO(response.content))
        return doc.get_contents_as_string()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsAttachments -v
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add apps/documents/source_loaders/json_collection.py apps/documents/tests/test_json_collection_loader.py
git commit -m "feat: JSONCollectionLoader processes items with attachments"
```

---

## Task 7: `_process_item` — fallback paths and optional metadata

**Files:**
- Modify: `apps/documents/tests/test_json_collection_loader.py`

The implementation already covers these — this task adds tests to lock the behaviour in.

- [ ] **Step 1: Write tests**

Append to `apps/documents/tests/test_json_collection_loader.py`:

```python
class TestLoadDocumentsFallback:
    def test_no_attachments_field_yields_fallback(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "date": "01/01/2025",
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)

        loader = _make_loader(json_config)
        docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "T"
        assert docs[0].metadata["source"] == "https://example.com/page"
        assert docs[0].metadata["title"] == "T"
        assert docs[0].metadata["URI"] == "https://example.com/page"
        assert docs[0].metadata["citation_text"] == "T"
        assert docs[0].metadata["citation_url"] == "https://example.com/page"
        assert "link" not in docs[0].metadata

    def test_empty_attachments_yields_fallback(self, json_config, httpx_mock):
        feed = [{"title": "T", "URI": "https://example.com/page", "attachments": []}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert len(docs) == 1
        assert docs[0].page_content == "T"

    def test_attachments_without_links_yields_fallback(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [{"file_type": "pdf", "file_size": "?"}],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert len(docs) == 1
        assert docs[0].page_content == "T"

    def test_optional_metadata_fields_propagate_when_present(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/p",
                "authors": "Alice",
                "publisher": "Pub",
                "countries": ["US", "CA"],
                "diseases": ["malaria"],
                "tags": ["health"],
                "regions": ["AFRO"],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        meta = docs[0].metadata
        for k, v in [
            ("authors", "Alice"),
            ("publisher", "Pub"),
            ("countries", ["US", "CA"]),
            ("diseases", ["malaria"]),
            ("tags", ["health"]),
            ("regions", ["AFRO"]),
        ]:
            assert meta[k] == v

    def test_optional_metadata_fields_absent_when_missing(self, json_config, httpx_mock):
        feed = [{"title": "T", "URI": "https://example.com/p"}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)
        meta = list(loader.load_documents())[0].metadata
        for k in ("authors", "publisher", "countries", "diseases", "tags", "regions"):
            assert k not in meta

    def test_item_missing_title_and_uri_is_skipped(self, json_config, httpx_mock, caplog):
        feed = [{"date": "01/01/2025"}, {"title": "OK", "URI": "https://example.com/p"}]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        loader = _make_loader(json_config)

        import logging as _logging
        with caplog.at_level(_logging.WARNING):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].metadata["title"] == "OK"
        assert any("neither 'title' nor 'URI'" in record.message for record in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsFallback -v
```

Expected: 6 tests pass without code changes (Task 6's implementation covers these).

- [ ] **Step 3: Commit**

```bash
git add apps/documents/tests/test_json_collection_loader.py
git commit -m "test: cover fallback paths and optional metadata propagation"
```

---

## Task 8: Per-attachment failure isolation

**Files:**
- Modify: `apps/documents/tests/test_json_collection_loader.py`

- [ ] **Step 1: Write tests**

Append to `apps/documents/tests/test_json_collection_loader.py`:

```python
class TestLoadDocumentsAttachmentFailures:
    def test_one_attachment_404_others_yielded(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "bad", "link": "https://example.com/bad.pdf"},
                    {"file_type": "pdf", "title": "good", "link": "https://example.com/good.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/bad.pdf", status_code=404, content=b"")
        httpx_mock.add_response(url="https://example.com/good.pdf", content=b"PDF")

        loader = _make_loader(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read",
            return_value=_stub_doc("good text"),
        ):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "good text"
        assert docs[0].metadata["link"] == "https://example.com/good.pdf"

    def test_extraction_error_skips_attachment(self, json_config, httpx_mock):
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "broken", "link": "https://example.com/x.pdf"},
                    {"file_type": "pdf", "title": "ok", "link": "https://example.com/y.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/x.pdf", content=b"junk")
        httpx_mock.add_response(url="https://example.com/y.pdf", content=b"ok")

        loader = _make_loader(json_config)
        with mock.patch(
            "apps.documents.source_loaders.json_collection.markitdown_read",
            side_effect=[RuntimeError("boom"), _stub_doc("ok text")],
        ):
            docs = list(loader.load_documents())

        assert len(docs) == 1
        assert docs[0].page_content == "ok text"
        assert docs[0].metadata["link"] == "https://example.com/y.pdf"

    def test_all_attachments_fail_yields_zero_documents_no_fallback(self, json_config, httpx_mock):
        """Transient failures must NOT produce a URI-keyed fallback that races
        against link-keyed Documents on subsequent syncs."""
        feed = [
            {
                "title": "T",
                "URI": "https://example.com/page",
                "attachments": [
                    {"file_type": "pdf", "title": "1", "link": "https://example.com/1.pdf"},
                    {"file_type": "pdf", "title": "2", "link": "https://example.com/2.pdf"},
                ],
            }
        ]
        httpx_mock.add_response(url="https://example.com/feed.json", json=feed)
        httpx_mock.add_response(url="https://example.com/1.pdf", status_code=500, content=b"")
        httpx_mock.add_response(url="https://example.com/2.pdf", status_code=500, content=b"")

        loader = _make_loader(json_config)
        docs = list(loader.load_documents())
        assert docs == []
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestLoadDocumentsAttachmentFailures -v
```

Expected: 3 tests pass — these exercise the existing try/except in `_yield_attachment_documents` from Task 6.

- [ ] **Step 3: Commit**

```bash
git add apps/documents/tests/test_json_collection_loader.py
git commit -m "test: cover per-attachment failure isolation and no-fallback rule"
```

---

## Task 9: `get_document_identifier` and `should_update_document`

**Files:**
- Modify: `apps/documents/source_loaders/json_collection.py`
- Test: `apps/documents/tests/test_json_collection_loader.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/documents/tests/test_json_collection_loader.py`:

```python
from langchain_core.documents import Document as LCDocument


class TestGetDocumentIdentifier:
    def test_attachment_doc_identifier_is_link(self, json_config):
        loader = _make_loader(json_config)
        doc = LCDocument(
            page_content="x",
            metadata={"link": "https://example.com/file.pdf", "source": "https://example.com/file.pdf"},
        )
        assert loader.get_document_identifier(doc) == "https://example.com/file.pdf"

    def test_fallback_doc_identifier_is_source(self, json_config):
        loader = _make_loader(json_config)
        doc = LCDocument(page_content="t", metadata={"source": "https://example.com/page"})
        assert loader.get_document_identifier(doc) == "https://example.com/page"


class TestShouldUpdateDocument:
    def _make_existing_file(self, metadata):
        existing = Mock()
        existing.file = Mock()
        existing.file.metadata = metadata
        return existing

    def test_same_date_means_no_update(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={"date": "01/01/2025"})
        existing = self._make_existing_file({"date": "01/01/2025"})
        assert loader.should_update_document(new_doc, existing) is False

    def test_different_date_means_update(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={"date": "02/01/2025"})
        existing = self._make_existing_file({"date": "01/01/2025"})
        assert loader.should_update_document(new_doc, existing) is True

    def test_missing_date_defers_to_base(self, json_config):
        loader = _make_loader(json_config)
        new_doc = LCDocument(page_content="x", metadata={})
        existing = self._make_existing_file({})
        # base class returns True (always update)
        assert loader.should_update_document(new_doc, existing) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestGetDocumentIdentifier apps/documents/tests/test_json_collection_loader.py::TestShouldUpdateDocument -v
```

Expected: `TestGetDocumentIdentifier` may pass for the fallback case (base class falls back to `source`); the attachment-link case passes too because `link == source` for attachment docs. `TestShouldUpdateDocument` will fail because the base class returns `True` even when dates match — confirms we need the override.

- [ ] **Step 3: Add the overrides to the loader**

Append to the `JSONCollectionLoader` class in `apps/documents/source_loaders/json_collection.py` (after the existing methods):

```python
    def get_document_identifier(self, document: Document) -> str:
        link = document.metadata.get("link")
        if link:
            return link
        return super().get_document_identifier(document)

    def should_update_document(self, document, existing_file) -> bool:
        new_date = document.metadata.get("date")
        old_date = existing_file.file.metadata.get("date") if existing_file.file else None
        if new_date and old_date:
            return new_date != old_date
        return super().should_update_document(document, existing_file)
```

(`get_document_identifier` is technically equivalent to the base behaviour for attachment docs given that we set `source = link`, but the explicit override makes the contract clearer and shields against future changes to either field.)

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_json_collection_loader.py::TestGetDocumentIdentifier apps/documents/tests/test_json_collection_loader.py::TestShouldUpdateDocument -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/documents/source_loaders/json_collection.py apps/documents/tests/test_json_collection_loader.py --fix
uv run ruff format apps/documents/source_loaders/json_collection.py apps/documents/tests/test_json_collection_loader.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/documents/source_loaders/json_collection.py apps/documents/tests/test_json_collection_loader.py
git commit -m "feat: JSONCollectionLoader identifier and date-based change detection"
```

---

## Task 10: `JSONCollectionDocumentSourceForm`

**Files:**
- Modify: `apps/documents/forms.py`
- Test: `apps/documents/tests/test_forms.py`

- [ ] **Step 1: Write failing tests**

Append to `apps/documents/tests/test_forms.py` (create the file if it doesn't already exist; if it does, append a new class):

```python
import pytest

from apps.documents.forms import JSONCollectionDocumentSourceForm
from apps.documents.models import SourceType
from apps.utils.factories.documents import CollectionFactory


@pytest.mark.django_db()
class TestJSONCollectionDocumentSourceForm:
    @pytest.fixture()
    def collection(self):
        return CollectionFactory.create()

    def test_valid_input_produces_config(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
            },
        )
        assert form.is_valid(), form.errors
        config = form.cleaned_data["config"]
        assert config.json_collection is not None
        assert str(config.json_collection.json_url) == "https://example.com/feed.json"
        assert config.json_collection.request_timeout == 30

    def test_invalid_url(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "not a url",
                "request_timeout": 30,
            },
        )
        assert not form.is_valid()
        assert "json_url" in form.errors

    def test_wrong_source_type_rejected(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.GITHUB,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
            },
        )
        assert not form.is_valid()
        assert "source_type" in form.errors

    def test_request_timeout_out_of_bounds(self, collection):
        form = JSONCollectionDocumentSourceForm(
            collection=collection,
            data={
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 1,
            },
        )
        assert not form.is_valid()
        assert "request_timeout" in form.errors

    def test_no_auth_field_rendered(self, collection):
        form = JSONCollectionDocumentSourceForm(collection=collection)
        assert "auth_provider" not in form.fields
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_forms.py::TestJSONCollectionDocumentSourceForm -v
```

Expected: `ImportError` for `JSONCollectionDocumentSourceForm`.

- [ ] **Step 3: Implement the form**

In `apps/documents/forms.py`:

a) Update the import line for datamodels to include the new config:

```python
from apps.documents.datamodels import (
    ConfluenceSourceConfig,
    DocumentSourceConfig,
    GitHubSourceConfig,
    JSONCollectionSourceConfig,
)
```

b) Append the new form class at the bottom of the file:

```python
class JSONCollectionDocumentSourceForm(DocumentSourceForm):
    requires_auth = False

    json_url = forms.URLField(
        label="JSON Feed URL",
        help_text="URL of the JSON feed to ingest (must be a JSON list of items)",
        widget=forms.URLInput(attrs={"placeholder": "https://example.com/feed.json"}),
    )
    request_timeout = forms.IntegerField(
        initial=30,
        min_value=5,
        max_value=300,
        label="Request Timeout (seconds)",
        help_text="HTTP timeout applied to the JSON fetch and each attachment download",
    )

    def _get_config_from_instance(self, instance):
        return instance.config.json_collection

    def clean_json_url(self):
        json_url = self.cleaned_data["json_url"]
        try:
            validate_user_input_url(json_url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise forms.ValidationError(f"The URL is invalid: {str(e)}") from None
        return json_url

    def clean_source_type(self):
        source_type = self.cleaned_data.get("source_type")
        if source_type != SourceType.JSON_COLLECTION:
            raise forms.ValidationError(f"Expected JSON Collection source type, got {source_type}")
        return source_type

    def clean(self):
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data

        try:
            json_collection_config = JSONCollectionSourceConfig(
                json_url=cleaned_data["json_url"],
                request_timeout=cleaned_data["request_timeout"],
            )
        except pydantic.ValidationError as e:
            raise forms.ValidationError(f"Invalid config: {str(e)}") from None

        cleaned_data["config"] = DocumentSourceConfig(json_collection=json_collection_config)
        return cleaned_data
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_forms.py::TestJSONCollectionDocumentSourceForm -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/documents/forms.py apps/documents/tests/test_forms.py --fix
uv run ruff format apps/documents/forms.py apps/documents/tests/test_forms.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/documents/forms.py apps/documents/tests/test_forms.py
git commit -m "feat: add JSONCollectionDocumentSourceForm"
```

---

## Task 11: View wiring + flag-based gating

**Files:**
- Modify: `apps/documents/views.py`
- Test: `apps/documents/tests/test_views.py`

The view must (a) include `JSONCollectionDocumentSourceForm` in its `source_type → form` map, (b) filter the JSON-collection entry out of the `document_source_types` context list when the flag is off, and (c) reject direct create/edit requests for `json_collection` when the flag is off (404).

- [ ] **Step 1: Write failing tests**

Append to `apps/documents/tests/test_views.py`. Match the existing fixture/factory style in that file:

```python
from waffle.testutils import override_flag

from apps.documents.models import SourceType


@pytest.mark.django_db()
class TestJSONCollectionSourceCreation:
    @pytest.fixture()
    def collection(self):
        team = TeamWithUsersFactory.create()
        return CollectionFactory.create(
            name="Tester",
            team=team,
            is_index=True,
            is_remote_index=True,
            llm_provider=LlmProviderFactory.create(team=team),
        )

    @override_flag("flag_json_collection_loader", active=True)
    def test_picker_includes_json_collection_when_flag_on(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:single_collection_home", args=[collection.team.slug, collection.id])
        response = client.get(url)
        assert response.status_code == 200
        assert SourceType.JSON_COLLECTION in response.context["document_source_types"]

    @override_flag("flag_json_collection_loader", active=False)
    def test_picker_excludes_json_collection_when_flag_off(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:single_collection_home", args=[collection.team.slug, collection.id])
        response = client.get(url)
        assert response.status_code == 200
        assert SourceType.JSON_COLLECTION not in response.context["document_source_types"]

    @override_flag("flag_json_collection_loader", active=True)
    def test_create_form_loads_when_flag_on(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:create_document_source", args=[collection.team.slug, collection.id])
        response = client.get(url, {"source_type": SourceType.JSON_COLLECTION})
        assert response.status_code == 200

    @override_flag("flag_json_collection_loader", active=False)
    def test_create_form_404s_when_flag_off(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:create_document_source", args=[collection.team.slug, collection.id])
        response = client.get(url, {"source_type": SourceType.JSON_COLLECTION})
        assert response.status_code == 404

    @override_flag("flag_json_collection_loader", active=False)
    def test_create_post_404s_when_flag_off(self, collection, client):
        client.force_login(collection.team.members.first())
        url = reverse("documents:create_document_source", args=[collection.team.slug, collection.id])
        response = client.post(
            url,
            {
                "source_type": SourceType.JSON_COLLECTION,
                "auto_sync_enabled": False,
                "json_url": "https://example.com/feed.json",
                "request_timeout": 30,
            },
        )
        assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/documents/tests/test_views.py::TestJSONCollectionSourceCreation -v
```

Expected: 5 failures — form not in map, flag not enforced, picker not filtered.

- [ ] **Step 3: Add `JSONCollectionDocumentSourceForm` to the form-class map**

In `apps/documents/views.py`, update imports:

```python
from apps.documents.forms import (
    ConfluenceDocumentSourceForm,
    DocumentSourceForm,
    GithubDocumentSourceForm,
    JSONCollectionDocumentSourceForm,
)
```

and update `BaseDocumentSourceView.get_form_class` (around line 215):

```python
    def get_form_class(self):
        return {
            SourceType.GITHUB: GithubDocumentSourceForm,
            SourceType.CONFLUENCE: ConfluenceDocumentSourceForm,
            SourceType.JSON_COLLECTION: JSONCollectionDocumentSourceForm,
        }.get(self.source_type, DocumentSourceForm)
```

- [ ] **Step 4: Add flag gating to `BaseDocumentSourceView.dispatch`**

In `apps/documents/views.py`, update imports to include the flag check:

```python
from waffle import flag_is_active

from apps.teams.flags import Flags
```

and edit the existing `dispatch` method on `BaseDocumentSourceView` (currently around line 230):

```python
    def dispatch(self, request, *args, **kwargs):
        if not self.collection.is_index:
            messages.error(request, "Document sources can only be configured for indexed collections.")
            return redirect("documents:single_collection_home", team_slug=self.team_slug, pk=self.collection_id)
        if (
            self.source_type == SourceType.JSON_COLLECTION
            and not flag_is_active(request, Flags.JSON_COLLECTION_LOADER.slug)
        ):
            raise Http404("JSON Collection source is not enabled for this team.")
        return super().dispatch(request, *args, **kwargs)
```

Add the `Http404` import at the top of the file if it isn't already imported:

```python
from django.http import Http404
```

- [ ] **Step 5: Filter the source-type picker by flag**

Find the function that renders `single_collection_home.html` (the one setting `"document_source_types": list(SourceType)`, around line 111). Replace that line with a flag-aware list:

```python
        "document_source_types": _visible_source_types(request),
```

and add this helper near the top of `apps/documents/views.py` (or just above the view):

```python
def _visible_source_types(request) -> list[SourceType]:
    types = list(SourceType)
    if not flag_is_active(request, Flags.JSON_COLLECTION_LOADER.slug):
        types = [t for t in types if t != SourceType.JSON_COLLECTION]
    return types
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest apps/documents/tests/test_views.py::TestJSONCollectionSourceCreation -v
```

Expected: 5 tests pass.

- [ ] **Step 7: Run the full views and forms test suite to catch regressions**

```bash
uv run pytest apps/documents/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Lint and format**

```bash
uv run ruff check apps/documents/views.py apps/documents/tests/test_views.py --fix
uv run ruff format apps/documents/views.py apps/documents/tests/test_views.py
uv run ty check apps/documents/
```

- [ ] **Step 9: Commit**

```bash
git add apps/documents/views.py apps/documents/tests/test_views.py
git commit -m "feat: wire JSONCollectionDocumentSourceForm into views and gate on flag"
```

---

## Task 12: End-to-end smoke test through `DocumentSourceManager`

**Files:**
- Test: `apps/documents/tests/test_document_sources.py`

This task verifies that the loader integrates with `DocumentSourceManager._sync_documents` and produces actual `CollectionFile` rows. Mirrors the existing test pattern in `test_document_sources.py`.

- [ ] **Step 1: Inspect existing patterns**

Read `apps/documents/tests/test_document_sources.py` to find the established pattern (mocking `create_loader` to inject a stub loader, asserting `CollectionFile` counts). Match that style.

- [ ] **Step 2: Write the smoke test**

Append a new test class to `apps/documents/tests/test_document_sources.py` (use the existing imports and factory patterns at the top of that file; add any missing imports as needed):

```python
from unittest import mock

import pytest

from apps.documents.datamodels import DocumentSourceConfig, JSONCollectionSourceConfig
from apps.documents.document_source_service import DocumentSourceManager
from apps.documents.models import CollectionFile, SourceType
from apps.documents.source_loaders.json_collection import JSONCollectionLoader
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory


@pytest.mark.django_db()
class TestJSONCollectionEndToEnd:
    def test_sync_creates_collection_files_for_each_document(self):
        from langchain_core.documents import Document as LCDocument

        collection = CollectionFactory.create(is_index=False)
        document_source = DocumentSourceFactory.create(
            collection=collection,
            source_type=SourceType.JSON_COLLECTION,
            config=DocumentSourceConfig(
                json_collection=JSONCollectionSourceConfig(json_url="https://example.com/feed.json"),
            ),
        )

        fake_docs = [
            LCDocument(
                page_content="text 1",
                metadata={
                    "source": "https://example.com/1.pdf",
                    "link": "https://example.com/1.pdf",
                    "title": "Doc 1",
                    "URI": "https://example.com/page1",
                    "date": "01/01/2025",
                },
            ),
            LCDocument(
                page_content="text 2",
                metadata={
                    "source": "https://example.com/2.pdf",
                    "link": "https://example.com/2.pdf",
                    "title": "Doc 2",
                    "URI": "https://example.com/page2",
                    "date": "01/01/2025",
                },
            ),
        ]

        with (
            mock.patch.object(JSONCollectionLoader, "load_documents", return_value=iter(fake_docs)),
            mock.patch(
                "apps.documents.document_source_service.DocumentSourceManager._index_files"
            ) as index_mock,
        ):
            result = DocumentSourceManager(document_source).sync_collection()

        assert result.success
        assert result.files_added == 2
        assert CollectionFile.objects.filter(collection=collection).count() == 2
        index_mock.assert_called_once()
```

If `DocumentSourceFactory` does not support setting `source_type` and `config` directly via factory kwargs, set them on the returned instance and call `.save()`. (Read the factory before assuming.)

- [ ] **Step 3: Run the smoke test**

```bash
uv run pytest apps/documents/tests/test_document_sources.py::TestJSONCollectionEndToEnd -v
```

Expected: passes.

- [ ] **Step 4: Lint and format**

```bash
uv run ruff check apps/documents/tests/test_document_sources.py --fix
uv run ruff format apps/documents/tests/test_document_sources.py
```

- [ ] **Step 5: Commit**

```bash
git add apps/documents/tests/test_document_sources.py
git commit -m "test: end-to-end smoke test for JSON collection sync"
```

---

## Task 13: Final verification

- [ ] **Step 1: Run the full documents app test suite**

```bash
uv run pytest apps/documents/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Type-check and lint the touched files**

```bash
uv run ty check apps/documents/
uv run ruff check apps/documents/ apps/teams/flags.py
uv run ruff format --check apps/documents/ apps/teams/flags.py
```

Expected: no errors.

- [ ] **Step 3: Manual sanity check (optional, recommended)**

Start the dev environment, navigate to a team with the flag enabled, create a Collection, then add a "JSON Collection" document source pointing at a small public JSON feed and confirm that the sync runs and creates `CollectionFile`s.

```bash
uv run inv runserver
```

- [ ] **Step 4: Open PR**

Use the project's PR template (`.github/pull_request_template.md`). Mark the migration as backwards-compatible (it is — choices change only).

---

## Self-Review

Spec coverage:

- ✅ New loader class added — Tasks 4, 6, 9
- ✅ Fetches JSON from configured URL — Task 5
- ✅ Maps indexed-collections format to Documents — Task 6, 7
- ✅ Follows attachment links and extracts PDF text — Task 6
- ✅ Item-level metadata propagated to attachment-derived docs — Task 6, 7
- ✅ Falls back gracefully when attachments empty / unreachable — Task 7, 8
- ✅ Configurable field mapping for `page_content` and `source` — **deferred per spec scope (Approach A)**
- ✅ Unit tests covering format and edge cases — Tasks 5–9
- ✅ Works with existing pipeline — Task 12 (end-to-end smoke test)
- ✅ Feature flag gates UI and creation — Tasks 1, 11

No placeholders detected. Method names (`_fetch_json_list`, `_process_item`, `_build_item_metadata`, `_yield_attachment_documents`, `_fetch_and_extract`, `get_document_identifier`, `should_update_document`) are consistent across tasks.

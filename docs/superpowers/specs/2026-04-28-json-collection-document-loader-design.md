# JSON Collection Document Loader — Design

GitHub issue: [dimagi/open-chat-studio#3176](https://github.com/dimagi/open-chat-studio/issues/3176)

## Summary

Add a new document source type, `json_collection`, that fetches a JSON feed from a configured URL, maps each item to one or more LangChain `Document` objects, and follows attachment links (PDFs etc.) to extract their text content. The first iteration targets the "indexed collections" JSON shape (e.g. ESPEN-style document libraries) only; the format mapping is hard-coded.

The loader plugs into the existing `apps/documents/source_loaders/` framework alongside `GitHubDocumentLoader` and `ConfluenceDocumentLoader`. It introduces no schema changes to `DocumentSource` or `CollectionFile`. UI exposure is gated behind a Waffle feature flag.

## Motivation

Some knowledge sources (e.g. ESPEN's document library) expose their content as a JSON array of items with metadata and attachment links. There is currently no loader for this pattern. Manual upload doesn't scale and doesn't capture the structured per-item metadata (type, languages, regions, diseases, etc.) that downstream RAG can use for citation and filtering.

## Scope

### In scope

- New `SourceType.JSON_COLLECTION` and `JSONCollectionLoader`.
- Hard-coded mapping of the "indexed collections" JSON shape.
- Per-attachment fetch and text extraction via the existing `markitdown_read` reader.
- Per-attachment failure isolation (one bad PDF doesn't fail the sync).
- Feature flag (`flag_json_collection_loader`) gating both UI and view-level access for source creation.
- Change detection via the item-level `date` field.

### Out of scope

- **Configurable field mapping.** Format is hard-coded for v1.
- **Authentication.** No auth on the JSON URL or attachment URLs.
- **Pagination.** The JSON response must contain the full list at the top level.
- **Nested or non-list root structures.** Loader raises if the root is not a JSON list.
- **Storing original attachment binaries.** Only extracted text is persisted, matching existing loaders.

## Indexed Collections JSON Shape

The loader expects a top-level JSON list. Each item:

```json
{
  "title": "Document Title",
  "URI": "https://example.com/document-page",
  "date": "08/04/2025",
  "type": "Meeting reports",
  "languages": ["en"],
  "attachments": [
    {
      "file_type": "pdf",
      "file_size": "364.15KB",
      "title": "Attachment title",
      "link": "https://example.com/file.pdf"
    }
  ]
}
```

Optional item-level fields propagated to metadata when present: `authors`, `publisher`, `countries`, `diseases`, `tags`, `regions`.

## Architecture

### Touch points (existing framework)

The collections framework already defines the extension surface; the loader follows the existing pattern.

| Existing component | Role | What changes |
|---|---|---|
| `BaseDocumentLoader` | Abstract iterator over `Document`s, plus `get_document_identifier` and `should_update_document` hooks | New subclass `JSONCollectionLoader` |
| `DocumentSourceManager._sync_documents` | Drives add/update/remove of `CollectionFile`s using loader hooks | Unchanged — works with the new loader as-is |
| `DocumentSourceConfig` (pydantic) | Holds per-source-type config | Adds optional `json_collection: JSONCollectionSourceConfig` |
| `SourceType` enum | DB-stored choice on `DocumentSource.source_type` | Adds `JSON_COLLECTION = "json_collection"` |
| `LOADERS` registry | `source_type → loader class` map | Registers `JSONCollectionLoader` |
| `DocumentSourceForm` subclasses | One per source type, owns its config schema and validation | Adds `JSONCollectionDocumentSourceForm` |
| Source-type picker template | Tile list shown when adding a source | Adds a "JSON Collection" tile, flag-gated |

### New / changed files

| File | Change |
|---|---|
| `apps/documents/source_loaders/json_collection.py` | **new** — `JSONCollectionLoader` |
| `apps/documents/datamodels.py` | add `JSONCollectionSourceConfig`; extend `DocumentSourceConfig` |
| `apps/documents/models.py` | add `SourceType.JSON_COLLECTION`; CSS-logo entry; extend `DocumentSource.source_config` |
| `apps/documents/source_loaders/registry.py` | register loader |
| `apps/documents/forms.py` | add `JSONCollectionDocumentSourceForm` |
| `apps/documents/views.py` | wire form into `source_type → form` map; gate creation view on the flag |
| `apps/teams/flags.py` | register `JSON_COLLECTION_LOADER` flag |
| `apps/documents/migrations/00XX_…` | **new** — `AlterField` for `DocumentSource.source_type` choices (Django emits one when `TextChoices` are extended) |
| `apps/documents/tests/test_json_collection_loader.py` | **new** — unit tests |
| `apps/documents/tests/data/indexed_collection_*.json` | **new** — fixture inputs |
| `templates/documents/...` (source-type picker) | add JSON Collection tile, `{% flag %}`-gated |

## Data Model

### `JSONCollectionSourceConfig`

```python
class JSONCollectionSourceConfig(pydantic.BaseModel):
    json_url: HttpUrl
    request_timeout: int = pydantic.Field(default=30, ge=5, le=300)

    def __str__(self) -> str:
        return str(self.json_url)
```

`request_timeout` is exposed because the loader may make many third-party HTTP calls per sync; a knob is cheap insurance against hangs.

### `DocumentSourceConfig`

```python
class DocumentSourceConfig(pydantic.BaseModel):
    github: GitHubSourceConfig | None = None
    confluence: ConfluenceSourceConfig | None = None
    json_collection: JSONCollectionSourceConfig | None = None
```

### `SourceType`

```python
class SourceType(models.TextChoices):
    GITHUB = "github", _("GitHub Repository")
    CONFLUENCE = "confluence", _("Confluence")
    JSON_COLLECTION = "json_collection", _("JSON Collection")
```

`css_logo` gets an entry for the new value (icon TBD, suggest `fa-solid fa-file-code`).

`DocumentSource.source_config` is extended:

```python
elif self.source_type == SourceType.JSON_COLLECTION:
    return self.config.json_collection
```

## Loader Behaviour

### `JSONCollectionLoader.for_document_source`

No auth provider required; constructs `cls(collection, document_source.config.json_collection, auth_provider=None)`.

### `load_documents() -> Iterator[Document]`

1. `httpx.get(self.config.json_url, timeout=self.config.request_timeout, follow_redirects=True)`.
2. `raise_for_status()`; parse JSON; require the body to be a list. If not, raise.
3. For each item:
    - Build **item metadata** from the item-level fields. Always-included fields when present: `title`, `URI`, `date`, `type`, `languages`. Optional fields when present: `authors`, `publisher`, `countries`, `diseases`, `tags`, `regions`. Plus the framework-required keys: `collection_id`, `source_type="json_collection"`, `citation_text=title`, `citation_url=URI`.
    - Compute `fetchable = [a for a in (attachments or []) if a.get("link")]`.
    - If `fetchable` is non-empty, iterate it. For each attachment:
        - Try `_fetch_and_extract(link)`. On success, yield a `Document` with `page_content=extracted_text` and `metadata = {**item_metadata, **attachment_metadata, "source": link}`. Attachment-level metadata is namespaced as `file_type`, `file_size`, `attachment_title` (renamed to avoid clobbering item `title`), and `link`.
        - On failure, log with item URI and attachment link, skip this attachment. **No fallback Document is emitted in this case** — a transient fetch error should not create a competing identifier (`URI`) that races against the attachment identifier (`link`) on subsequent syncs. The next successful sync will yield the attachment-derived Document.
    - Else (`fetchable` is empty — no attachments at all, or none with a `link`):
        - If `title` is present, yield a fallback `Document` with `page_content=title`, `metadata = {**item_metadata, "source": URI}`.
        - If `title` is also missing, log and skip the item.

### `_fetch_and_extract(url) -> str`

```python
response = httpx.get(url, timeout=self.config.request_timeout, follow_redirects=True)
response.raise_for_status()
doc = markitdown_read(BytesIO(response.content))
return doc.get_contents_as_string()
```

Raises on any HTTP, timeout, or extraction error; the caller catches, logs, and continues.

### `get_document_identifier(document) -> str`

- Attachment-derived `Document`: returns `document.metadata["link"]`.
- Fallback `Document` (no attachments): returns `document.metadata["source"]` (the item `URI`).
- Falls back to base behaviour (`source` field) if neither is present.

### `should_update_document(document, existing_file) -> bool`

- Compare new `document.metadata["date"]` against `existing_file.file.metadata.get("date")`.
- Both present and equal → `False` (skip update).
- Otherwise → `True` (re-fetch and re-extract).

### Failure mode summary

| Condition | Behaviour |
|---|---|
| JSON URL unreachable / non-2xx | Raise; whole sync fails (existing wrapper logs and records) |
| Top-level JSON not a list | Raise; whole sync fails |
| Single item missing `title` and `URI` | Log, skip item, continue |
| Item has `attachments=[]`, no `attachments`, or no entries with a `link` | Yield fallback Document from `title`/`URI` |
| Single attachment fetch fails (HTTP, timeout) | Log, skip attachment, continue (no fallback for that attachment) |
| Single attachment text extraction fails | Log, skip attachment, continue (no fallback for that attachment) |
| All attachments of an item fail to fetch | Item produces zero Documents this sync; will retry next sync |

## Form & UI

### `JSONCollectionDocumentSourceForm`

- `requires_auth = False` (no `auth_provider` field rendered).
- `json_url: URLField` with `validate_user_input_url(strict=not settings.DEBUG)` in `clean_json_url`.
- `request_timeout: IntegerField` (default 30, min 5, max 300).
- `clean_source_type` asserts `SourceType.JSON_COLLECTION`.
- `clean()` builds `JSONCollectionSourceConfig` and wraps in `DocumentSourceConfig(json_collection=...)`.
- `_get_config_from_instance(instance)` returns `instance.config.json_collection`.

### Views

`apps/documents/views.py` adds the form to the `source_type → form` map (currently around line 217). The view that renders the source-creation form additionally checks the feature flag for the JSON collection case and returns 404 when the flag is inactive — this prevents bypass via direct POST.

### Template

The source-type picker template adds a "JSON Collection" tile next to GitHub and Confluence, wrapped in `{% flag "flag_json_collection_loader" %}…{% endflag %}` so it only appears for teams that have the flag enabled.

## Feature Flag

Register in `apps/teams/flags.py`:

```python
JSON_COLLECTION_LOADER = (
    "flag_json_collection_loader",
    "JSON Collection document source loader",
    "",
    [],
    True,  # teams_can_manage
)
```

Scope of the flag:
- **Gated:** UI tile in the source-type picker, view-level access to the JSON-collection creation form.
- **Not gated:** the loader itself, the registry entry, scheduled syncs of already-created sources. Disabling the flag for a team that already has a source must not break their ongoing sync.

## Testing

### Unit tests — `apps/documents/tests/test_json_collection_loader.py`

HTTP is mocked with `respx` (or `httpx.MockTransport` if `respx` isn't already a dep). Fixtures live under `apps/documents/tests/data/`:

- `indexed_collection_full.json` — items with attachments and all optional fields.
- `indexed_collection_minimal.json` — items with only `title` and `URI`.
- `indexed_collection_mixed.json` — mix of with-attachments, no-attachments, and malformed items.

Cases:

1. Multi-attachment item produces N `Document`s, each with merged metadata and `source = attachment.link`.
2. No-attachment item produces a single fallback `Document` with `page_content = title` and `source = URI`.
3. All optional fields propagate when present and are absent when not.
4. Attachment HTTP 404 → that attachment skipped, others yielded, no exception bubbles.
5. Attachment extraction raises (markitdown error) → that attachment skipped, others yielded.
6. Item with one attachment whose fetch fails → zero Documents yielded for the item, no fallback. (Avoids identifier churn on subsequent syncs.)
7. Item with attachments list where no entry has a `link` → fallback Document yielded from `title`/`URI`.
8. Item missing both `title` and `URI` → skipped with a log entry.
9. Top-level JSON not a list → raises.
10. JSON fetch HTTP error → raises.
11. `get_document_identifier` returns the attachment link for attachment docs and the URI for fallback docs.
12. `should_update_document` — equal `date` → `False`; different `date` → `True`; missing `date` → defers to base (`True`).

### Integration smoke test

A test using the existing `DocumentSourceManager._sync_documents` pattern (similar to existing GitHub/Confluence tests) with a stubbed `JSONCollectionLoader.load_documents` to confirm `CollectionFile` rows get created end-to-end.

### View tests

Parametrise the source-creation view tests over flag on / flag off. With the flag off the view returns 404 for `source_type=json_collection` even on direct POST.

## Acceptance Criteria Mapping

Mapping back to issue #3176:

| AC | Where addressed |
|---|---|
| New loader class added | `JSONCollectionLoader` |
| Fetches JSON from configured URL | `load_documents()` step 1 |
| Maps indexed-collections format to `Document`s with metadata | `load_documents()` step 3 |
| Follows attachment links and extracts PDF text | `_fetch_and_extract()` via `markitdown_read` |
| Item-level metadata propagates to attachment-derived docs | `metadata = {**item_metadata, **attachment_metadata, ...}` |
| Falls back gracefully when attachments empty / unreachable | Fallback Document path; per-attachment skip-and-log |
| Configurable field mapping for `page_content` and `source` | **Deferred** (Approach A; future work — out of scope for v1) |
| Unit tests | `test_json_collection_loader.py` |
| Works with existing pipeline | Conforms to `BaseDocumentLoader` interface; `DocumentSourceManager` unchanged |

## Open questions / risks

- **`httpx` dependency.** Confirm `httpx` is on the project's dependency list during implementation; fall back to `requests` if not. (Either is fine — pick whichever is already there.)
- **Icon choice.** `fa-solid fa-file-code` is a placeholder; pick whatever fits the existing visual convention during implementation.
- **Migration.** `DocumentSource.source_type` is a `CharField` with `choices=SourceType.choices`, so `makemigrations` will emit an `AlterField` migration. This is a metadata-only migration (no DB schema change beyond updated `choices`) and is backwards-compatible.

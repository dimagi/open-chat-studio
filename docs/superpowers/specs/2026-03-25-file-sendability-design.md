# File Sendability Design

**Issue:** https://github.com/dimagi/open-chat-studio/issues/3088

## Problem

When users upload files to media collections, they have no visibility into whether those files can be sent directly on specific messaging channels. Files that exceed channel limits silently fall back to download links. Additionally, TwilioService uses a flat 16MB file size cap instead of the actual Meta Cloud API limits that WhatsApp enforces.

## Solution Overview

1. Extract file-sending limit logic into shared functions (single source of truth)
2. Store per-channel sendability on `CollectionFile` at upload time
3. Display unsupported-channel badges and a collection-level banner in the UI
4. Align TwilioService with Meta Cloud API limits
5. Provide a management command to backfill existing collections

## Detailed Design

### 1. Extracted File Limit Functions

**New file:** `apps/service_providers/file_limits.py`

Standalone functions that encode the file-sending constraints for each multimedia channel. Each function returns a `SendabilityResult` (NamedTuple with `.supported` and `.reason` fields):

```python
from typing import NamedTuple

class SendabilityResult(NamedTuple):
    supported: bool
    reason: str

def can_send_on_whatsapp(content_type: str, content_size: int) -> SendabilityResult:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""

def can_send_on_telegram(content_type: str, content_size: int) -> SendabilityResult:
    """Telegram limits: 10MB images, 50MB audio/video/documents."""

def can_send_on_slack(content_type: str, content_size: int) -> SendabilityResult:
    """Slack limit: 50MB for all supported types (image/*, video/*, audio/*, application/*)."""
```

These functions take raw `content_type` and `content_size` (bytes) rather than a `File` object, so they can be called from both the messaging services and the sendability checker without coupling to the model.

**Edge case handling:** If `content_type` is empty or `content_size` is falsy (0 or None), each function returns `SendabilityResult(False, "File type or size unknown")`. This is handled as a guard clause in each function, consistent with existing behavior in `MetaCloudAPIService.can_send_file()`.

#### Channel Registry

A `CHANNEL_CHECKS` dict maps channel names to their checker functions:

```python
CHANNEL_CHECKS: dict[str, Callable[[str, int], SendabilityResult]] = {
    "whatsapp": can_send_on_whatsapp,
    "telegram": can_send_on_telegram,
    "slack": can_send_on_slack,
}
```

This registry is the single source of truth for which channels are checked. `update_supported_channels()` iterates it, and templates can iterate the stored result generically. Adding a new channel requires only adding one entry here.

#### MIME Type Coverage

Each function uses MIME prefix matching (e.g., `image/*`, `audio/*`) consistent with the existing `MetaCloudAPIService` and `TelegramChannel` implementations. The supported MIME prefixes per channel:

- **WhatsApp:** `image/`, `audio/`, `video/`, `application/` — matches current `MetaCloudAPIService.can_send_file()` behavior. Note: `text/` types are excluded as the Meta Cloud API does not list them as supported media types.
- **Telegram:** `image/`, `audio/`, `video/`, `application/` — matches current `TelegramChannel._can_send_file()` behavior.
- **Slack:** `image/`, `audio/`, `video/`, `application/` — matches current `SlackChannel._can_send_file()` behavior, with a hardcoded 50MB limit (from `settings.MAX_FILE_SIZE_MB`).

#### Channels Included and Excluded

Only channels that support sending multimedia files directly are included in sendability checks:

| Channel | Included | Reason |
|---------|----------|--------|
| WhatsApp | Yes | Sends files via Meta Cloud API with type-based size limits |
| Telegram | Yes | Sends files via Bot API with type-based size limits |
| Slack | Yes | Sends files via Slack SDK with size limit |
| Web | No | Files served via web response, no external API limits |
| API | No | Files served via API response, no external API limits |
| Facebook Messenger | No | Limited file support, not a primary use case for media collections |
| SureAdhere | No | Specialized channel, not a general-purpose file channel |
| CommCare Connect | No | Specialized channel |
| Evaluations | No | Internal-only channel |
| Embedded Widget | No | Files served via web, no external API limits |

**Consumers updated to delegate to these functions:**
- `MetaCloudAPIService.can_send_file()` -> calls `can_send_on_whatsapp()`, uses the `.supported` result
- `TwilioService.can_send_file()` -> calls `can_send_on_whatsapp()` (replacing flat 16MB + MIME allowlist)
- `TurnIOService.can_send_file()` -> calls `can_send_on_whatsapp()`
- `TelegramChannel._can_send_file()` -> calls `can_send_on_telegram()`
- `SlackChannel._can_send_file()` -> calls `can_send_on_slack()`

#### TwilioService MIME Type Change

The current `TwilioService.can_send_file()` uses a strict MIME allowlist from `supported_mime_types.py` (only specific types like `image/jpeg`, `image/png`, `audio/amr`, `application/pdf`, etc.). After this change, it will use broad MIME prefix matching via `can_send_on_whatsapp()`, consistent with `MetaCloudAPIService`.

This means Twilio will now accept MIME types it previously rejected (e.g., `image/gif`, `audio/wav`). This is intentional — the Meta Cloud API (the ultimate destination) accepts these types, so the Twilio-specific allowlist was overly restrictive.

`supported_mime_types.py` should be deleted — its only consumer is `TwilioService.can_send_file()` at line 249 of `messaging_service.py`.

### 2. Data Model

**Modified model:** `CollectionFile` in `apps/documents/models.py`

Add a `supported_channels` JSONField:

```python
supported_channels = models.JSONField(
    default=dict,
    blank=True,
    help_text="Channels that cannot send this file directly, with reasons",
)
```

**Stored format — only unsupported channels are stored:**
```json
{
  "telegram": {"reason": "Exceeds 10MB image limit"}
}
```

If a channel key is absent, the file is sendable on that channel. An empty dict (`{}`) means the file is sendable on all channels. This makes template iteration simple (every entry = a badge to render) and the banner query trivial (`supported_channels != {}`).

**Method on `CollectionFile`:**

```python
def update_supported_channels(self):
    """Compute and set supported_channels from the file's content type and size.
    Uses a lazy import from apps.service_providers.file_limits to avoid
    module-level cross-app coupling (consistent with project patterns).
    """
```

This method iterates the `CHANNEL_CHECKS` registry and stores entries only for unsupported channels. Only called for files in non-indexed collections (`is_index=False`).

**Migration:** Adding a JSONField with `default=dict` and `blank=True` is backward-compatible and safely reversible.

### 3. Upload Integration

When files are uploaded to a **non-indexed** (media) collection, `update_supported_channels()` is called on each `CollectionFile` instance **in-memory before `bulk_create`**. Since the method only sets `self.supported_channels` (no DB access), this works with the existing `bulk_create` pattern in `views.py:393-398` without requiring individual saves or extra queries.

Indexed collections (`is_index=True`) are excluded because their files are chunked and searched via RAG, not sent directly over channels.

### 4. UI Changes

#### Collection-Level Banner

In `templates/documents/single_collection_home.html`, for non-indexed collections, query whether any `CollectionFile` in the collection has a non-empty `supported_channels`. If so, display a warning banner:

> "Some files in this collection cannot be sent directly on certain channels and will be sent as links instead. See individual file badges for details."

This uses a single `.exists()` query: `CollectionFile.objects.filter(collection=collection).exclude(supported_channels={}).exists()`. The `collection` FK index narrows the scan and `.exists()` short-circuits at the first match. The template already has `{% if collection.is_index %}` / `{% else %}` blocks that can be leveraged.

#### File-Level Badges

In `templates/documents/partials/collection_files.html`, for each file in a non-indexed collection, iterate `supported_channels` and show a badge per entry. If the dict is empty, no badges appear.

**Layout per file row:**
```
my_photo.png  4.2 MB  Not supported on: [WhatsApp] [Telegram]
```

- "Not supported on:" text label precedes the badges (only shown when there are unsupported channels)
- Each badge uses DaisyUI `badge badge-warning badge-sm` styling
- Tooltip on each badge shows the specific reason (e.g., "Exceeds 5MB image limit for WhatsApp. It will be sent as a link instead.")

### 5. TwilioService Alignment

`TwilioService.can_send_file()` currently uses:
- Flat `max_file_size_mb = 16` for all types
- Separate MIME type allowlist from `supported_mime_types.py`

This changes to delegate to `can_send_on_whatsapp()`, which applies Meta Cloud API type-based limits (5MB images, 16MB audio/video, 100MB docs) and MIME prefix matching.

**Impact:** Images between 5-16MB that were previously sent directly will now fall back to links. This is correct — Meta Cloud API rejects them regardless. Additionally, MIME types not in the previous Twilio allowlist but supported by Meta Cloud API will now be accepted (see Section 1).

### 6. Management Command

**Command:** `python manage.py populate_supported_channels`

**Arguments:**
- `--collection-id ID` — process a single collection
- `--team SLUG` — process all media collections for a team
- `--dry-run` — report what would change without writing
- At least one of `--collection-id` or `--team` is required (no unfiltered full-table runs)

**Behavior:**
- Filters to `is_index=False` collections only
- Calls `update_supported_channels()` on each `CollectionFile`
- Bulk updates the `supported_channels` field
- Idempotent: overwrites existing data on re-run
- Outputs progress: number of files processed, number with unsupported channels

**Note:** If channel limits change in the future (e.g., WhatsApp increases image size limit), update `file_limits.py` and re-run this command for affected teams to refresh stored data.

### 7. Testing

#### `file_limits.py` unit tests (no `@pytest.mark.django_db`)

Pure unit tests — these functions take `(str, int)` and return `SendabilityResult`, no Django dependencies:

- Parametrized tests for each `can_send_on_*` function
- Boundary: exactly at limit (e.g., 5,242,880 bytes for WhatsApp image) -> supported
- Boundary: 1 byte over limit -> unsupported with descriptive reason
- `content_size = 0` -> unsupported ("File type or size unknown")
- `content_type = ""` -> unsupported ("File type or size unknown")
- Unexpected MIME prefix (e.g., `text/plain`, `font/woff2`) -> unsupported
- Verify reason strings contain meaningful info (not empty for unsupported results)

#### `update_supported_channels()` tests (`@pytest.mark.django_db`)

Use `FileFactory` and `CollectionFileFactory` from `apps/utils/factories/`:

- Verify correct JSON output for various file types and sizes
- Verify only unsupported channels appear in the dict
- Verify empty dict for files sendable on all channels
- Verify reason strings match expected format

#### TwilioService regression tests

Specific behavioral change scenarios:

- Image at 6MB: previously sent directly, now falls back to link (size regression)
- `image/gif`: previously rejected by MIME allowlist, now accepted (MIME expansion)
- `audio/ogg`: previously rejected, now accepted
- `application/zip`: previously rejected, now accepted

#### Management command tests (`@pytest.mark.django_db`)

- `--collection-id` with valid media collection -> processes files
- `--collection-id` pointing to an indexed collection -> skips with warning
- `--collection-id` with non-existent ID -> error
- `--team` with mix of indexed and non-indexed -> only processes non-indexed
- No args -> error
- `--dry-run` -> reports without writing
- Idempotency: run twice, verify same result

#### View/template tests

- Banner appears when files have unsupported channels in non-indexed collections
- Banner does not appear when all files are sendable
- Badges render correctly for non-indexed collections
- Nothing renders for indexed collections

### 8. Performance Note

The `collection_files_view` queryset (`views.py:123`) currently lacks `select_related("file")`, causing an N+1 query (10 extra queries per page). Since this view is being modified, add `.select_related("file")` to the queryset.

## Files Changed

| File | Change |
|------|--------|
| `apps/service_providers/file_limits.py` | New — `SendabilityResult` NamedTuple, `can_send_on_*` functions, `CHANNEL_CHECKS` registry |
| `apps/service_providers/messaging_service.py` | Delegate `can_send_file()` to extracted functions |
| `apps/service_providers/supported_mime_types.py` | Delete — only consumer is being changed |
| `apps/chat/channels.py` | Delegate `_can_send_file()` to extracted functions |
| `apps/documents/models.py` | Add `supported_channels` field and `update_supported_channels()` |
| `apps/documents/views.py` | Call `update_supported_channels()` before `bulk_create`; banner query; add `select_related("file")` |
| `apps/documents/management/commands/populate_supported_channels.py` | New — backfill command |
| `templates/documents/single_collection_home.html` | Banner for unsupported files |
| `templates/documents/partials/collection_files.html` | Per-file channel badges |
| `apps/documents/migrations/XXXX_add_supported_channels.py` | Migration — backward-compatible, reversible |
| `apps/service_providers/tests/test_file_limits.py` | New — pure unit tests for limit functions |
| `apps/service_providers/tests/test_messaging_providers.py` | Update Twilio tests with regression scenarios |
| `apps/documents/tests/test_sendability.py` | New — `update_supported_channels()` + command tests |

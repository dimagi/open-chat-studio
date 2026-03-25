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

Standalone functions that encode the file-sending constraints for each multimedia channel. Each function returns a `tuple[bool, str]` — the boolean indicates sendability, and the string provides a human-readable reason when unsupported (empty string when supported):

```python
def can_send_on_whatsapp(content_type: str, content_size: int) -> tuple[bool, str]:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""

def can_send_on_telegram(content_type: str, content_size: int) -> tuple[bool, str]:
    """Telegram limits: 10MB images, 50MB audio/video/documents."""

def can_send_on_slack(content_type: str, content_size: int) -> tuple[bool, str]:
    """Slack limit: 50MB for all supported types (image/*, video/*, audio/*, application/*)."""
```

These functions take raw `content_type` and `content_size` (bytes) rather than a `File` object, so they can be called from both the messaging services and the sendability checker without coupling to the model.

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
- `MetaCloudAPIService.can_send_file()` -> calls `can_send_on_whatsapp()`, uses the boolean result
- `TwilioService.can_send_file()` -> calls `can_send_on_whatsapp()` (replacing flat 16MB + MIME allowlist)
- `TurnIOService.can_send_file()` -> calls `can_send_on_whatsapp()`
- `TelegramChannel._can_send_file()` -> calls `can_send_on_telegram()`
- `SlackChannel._can_send_file()` -> calls `can_send_on_slack()`

#### TwilioService MIME Type Change

The current `TwilioService.can_send_file()` uses a strict MIME allowlist from `supported_mime_types.py` (only specific types like `image/jpeg`, `image/png`, `audio/amr`, `application/pdf`, etc.). After this change, it will use broad MIME prefix matching via `can_send_on_whatsapp()`, consistent with `MetaCloudAPIService`.

This means Twilio will now accept MIME types it previously rejected (e.g., `image/gif`, `audio/wav`). This is intentional — the Meta Cloud API (the ultimate destination) accepts these types, so the Twilio-specific allowlist was overly restrictive.

The `supported_mime_types.py` file's `TWILIO` list will no longer be used by `can_send_file()`. If it has no other consumers, it should be removed.

### 2. Data Model

**Modified model:** `CollectionFile` in `apps/documents/models.py`

Add a `supported_channels` JSONField:

```python
supported_channels = models.JSONField(
    default=dict,
    blank=True,
    help_text="Mapping of channel type to sendability status for this file",
)
```

**Stored format:**
```json
{
  "whatsapp": {"supported": true, "reason": ""},
  "telegram": {"supported": false, "reason": "Exceeds 10MB image limit"},
  "slack": {"supported": true, "reason": ""}
}
```

**Method on `CollectionFile`:**

```python
def populate_sendability(self):
    """Compute and set supported_channels from the file's content type and size."""
```

This method calls each `can_send_on_*` function and stores the result with the reason string. Only called for files in non-indexed collections (`is_index=False`).

**Migration:** Adding a JSONField with `default=dict` and `blank=True` is backward-compatible and safely reversible.

### 3. Upload Integration

When files are uploaded to a **non-indexed** (media) collection, `populate_sendability()` is called before saving the `CollectionFile`. This happens in the upload path in `apps/documents/views.py`.

Indexed collections (`is_index=True`) are excluded because their files are chunked and searched via RAG, not sent directly over channels.

### 4. UI Changes

#### Collection-Level Banner

In `templates/documents/single_collection_home.html`, for non-indexed collections, query whether any `CollectionFile` in the collection has a channel with `supported: false`. If so, display a warning banner:

> "Some files in this collection cannot be sent directly on certain channels and will be sent as links instead. See individual file badges for details."

This uses a single `.exists()` query with a JSON lookup on `CollectionFile` at render time (no aggregate field stored on `Collection`). The template already has `{% if collection.is_index %}` / `{% else %}` blocks that can be leveraged.

#### File-Level Badges

In `templates/documents/partials/collection_files.html`, for each file in a non-indexed collection, show badges for unsupported channels only. If a file is sendable on all channels, no badges appear.

**Layout per file row:**
```
my_photo.png  4.2 MB  Not supported on: [WhatsApp] [Telegram]
```

- "Not supported on:" text label precedes the badges
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
- Calls `populate_sendability()` on each `CollectionFile`
- Bulk updates the `supported_channels` field
- Idempotent: overwrites existing data on re-run
- Outputs progress: number of files processed, number with unsupported channels

### 7. Testing

- **`file_limits.py` unit tests:** Parametrized tests for each `can_send_on_*` function — boundary sizes (exactly at limit, 1 byte over), unsupported MIME types, return value format `(bool, str)`
- **`populate_sendability()` tests:** Verify correct JSON output for various file types and sizes, including reason strings
- **TwilioService tests:** Update existing tests to reflect new type-based limits instead of flat 16MB
- **Management command tests:** `--collection-id`, `--team`, no args (error), `--dry-run`, idempotency verification
- **View/template tests:** Banner appears when files have unsupported channels, badges render correctly for non-indexed collections, nothing renders for indexed collections

## Files Changed

| File | Change |
|------|--------|
| `apps/service_providers/file_limits.py` | New — extracted limit functions returning `tuple[bool, str]` |
| `apps/service_providers/messaging_service.py` | Delegate `can_send_file()` to extracted functions |
| `apps/chat/channels.py` | Delegate `_can_send_file()` to extracted functions |
| `apps/documents/models.py` | Add `supported_channels` field and `populate_sendability()` |
| `apps/documents/views.py` | Call `populate_sendability()` on upload; banner query |
| `apps/documents/management/commands/populate_supported_channels.py` | New — backfill command |
| `templates/documents/single_collection_home.html` | Banner for unsupported files |
| `templates/documents/partials/collection_files.html` | Per-file channel badges |
| `apps/documents/migrations/XXXX_add_supported_channels.py` | Migration — backward-compatible, reversible |
| `apps/service_providers/tests/test_file_limits.py` | New — limit function tests |
| `apps/service_providers/tests/test_messaging_providers.py` | Update Twilio tests |
| `apps/documents/tests/test_sendability.py` | New — sendability + command tests |

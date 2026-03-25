# File Sendability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show users which messaging channels cannot send their files directly, and align Twilio with Meta Cloud API limits.

**Architecture:** Extract file-sending limit logic into pure functions in `file_limits.py` with a channel registry. Store unsupported-channel data on `CollectionFile` at upload time. Display badges and banners in the collection UI.

**Tech Stack:** Django, PostgreSQL JSONField, DaisyUI, HTMX, pytest

**Spec:** `docs/superpowers/specs/2026-03-25-file-sendability-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `apps/service_providers/file_limits.py` | **New.** `SendabilityResult` NamedTuple, `can_send_on_*` functions, `CHANNEL_CHECKS` registry |
| `apps/service_providers/messaging_service.py` | **Modify.** Delegate `can_send_file()` to `file_limits.py`; remove `supported_mime_types` import |
| `apps/service_providers/supported_mime_types.py` | **Delete.** Only consumer is being changed |
| `apps/chat/channels.py` | **Modify.** Delegate `_can_send_file()` to `file_limits.py` |
| `apps/documents/models.py` | **Modify.** Add `supported_channels` JSONField and `update_supported_channels()` to `CollectionFile` |
| `apps/documents/views.py` | **Modify.** Call `update_supported_channels()` before `bulk_create`; add banner query; add `select_related("file")` |
| `apps/documents/management/commands/populate_supported_channels.py` | **New.** Backfill management command |
| `templates/documents/single_collection_home.html` | **Modify.** Add warning banner for unsupported files |
| `templates/documents/partials/collection_files.html` | **Modify.** Add per-file channel badges |
| `apps/documents/migrations/XXXX_add_supported_channels.py` | **New.** Auto-generated migration |
| `apps/service_providers/tests/test_file_limits.py` | **New.** Pure unit tests for limit functions |
| `apps/service_providers/tests/test_messaging_providers.py` | **Modify.** Update Twilio tests, add regression scenarios |
| `apps/documents/tests/test_sendability.py` | **New.** `update_supported_channels()` + management command tests |

---

### Task 1: Create `file_limits.py` with `SendabilityResult` and WhatsApp checker

**Files:**
- Create: `apps/service_providers/file_limits.py`
- Create: `apps/service_providers/tests/test_file_limits.py`

- [ ] **Step 1: Write failing tests for `can_send_on_whatsapp`**

Create `apps/service_providers/tests/test_file_limits.py`:

```python
import pytest

from apps.service_providers.file_limits import SendabilityResult, can_send_on_whatsapp

MB = 1024 * 1024


class TestCanSendOnWhatsapp:
    """Tests for WhatsApp (Meta Cloud API) file limits."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            # Images: 5MB limit
            ("image/jpeg", 1 * MB, True),
            ("image/png", 5 * MB, True),  # exactly at limit
            ("image/gif", 5 * MB + 1, False),  # 1 byte over
            ("image/jpeg", 6 * MB, False),
            # Audio: 16MB limit
            ("audio/mpeg", 1 * MB, True),
            ("audio/ogg", 16 * MB, True),  # exactly at limit
            ("audio/wav", 16 * MB + 1, False),  # 1 byte over
            # Video: 16MB limit
            ("video/mp4", 16 * MB, True),  # exactly at limit
            ("video/mp4", 17 * MB, False),
            # Documents: 100MB limit
            ("application/pdf", 50 * MB, True),
            ("application/pdf", 100 * MB, True),  # exactly at limit
            ("application/zip", 100 * MB + 1, False),  # 1 byte over
            # Unsupported MIME types
            ("text/plain", 1024, False),
            ("font/woff2", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_whatsapp(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_unsupported_has_reason(self):
        result = can_send_on_whatsapp("image/jpeg", 6 * MB)
        assert result.supported is False
        assert result.reason  # reason must not be empty

    def test_supported_has_empty_reason(self):
        result = can_send_on_whatsapp("image/jpeg", 1 * MB)
        assert result.supported is True
        assert result.reason == ""

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [
            ("", 1024),
            ("image/jpeg", 0),
            ("", 0),
        ],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_whatsapp(content_type, content_size)
        assert result.supported is False
        assert "unknown" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_file_limits.py -v`
Expected: ImportError — `file_limits` module does not exist yet.

- [ ] **Step 3: Implement `SendabilityResult` and `can_send_on_whatsapp`**

Create `apps/service_providers/file_limits.py`:

```python
from collections.abc import Callable
from typing import NamedTuple

MB = 1024 * 1024


class SendabilityResult(NamedTuple):
    supported: bool
    reason: str


def can_send_on_whatsapp(content_type: str, content_size: int) -> SendabilityResult:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""
    if not content_type or not content_size:
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        limit = 5 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB image limit for WhatsApp")

    if content_type.startswith(("video/", "audio/")):
        limit = 16 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        media_type = "video" if content_type.startswith("video/") else "audio"
        return SendabilityResult(False, f"Exceeds {limit // MB}MB {media_type} limit for WhatsApp")

    if content_type.startswith("application/"):
        limit = 100 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB document limit for WhatsApp")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for WhatsApp")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/service_providers/tests/test_file_limits.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py --fix && uv run ruff format apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py`

- [ ] **Step 6: Commit**

```bash
git add apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py
git commit -m "feat: add SendabilityResult and can_send_on_whatsapp in file_limits.py"
```

---

### Task 2: Add Telegram and Slack checkers + CHANNEL_CHECKS registry

**Files:**
- Modify: `apps/service_providers/file_limits.py`
- Modify: `apps/service_providers/tests/test_file_limits.py`

- [ ] **Step 1: Write failing tests for `can_send_on_telegram` and `can_send_on_slack`**

Append to `apps/service_providers/tests/test_file_limits.py`:

```python
from apps.service_providers.file_limits import (
    CHANNEL_CHECKS,
    can_send_on_slack,
    can_send_on_telegram,
)


class TestCanSendOnTelegram:
    """Tests for Telegram Bot API file limits."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            # Images: 10MB limit
            ("image/jpeg", 1 * MB, True),
            ("image/png", 10 * MB, True),  # exactly at limit
            ("image/gif", 10 * MB + 1, False),  # 1 byte over
            # Audio/Video/Docs: 50MB limit
            ("audio/mpeg", 50 * MB, True),  # exactly at limit
            ("video/mp4", 50 * MB + 1, False),  # 1 byte over
            ("application/pdf", 50 * MB, True),
            # Unsupported MIME types
            ("text/plain", 1024, False),
            ("font/woff2", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_telegram(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_unsupported_has_reason(self):
        result = can_send_on_telegram("image/jpeg", 11 * MB)
        assert result.supported is False
        assert result.reason

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [("", 1024), ("image/jpeg", 0)],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_telegram(content_type, content_size)
        assert result.supported is False
        assert "unknown" in result.reason.lower()


class TestCanSendOnSlack:
    """Tests for Slack file limits (50MB for all supported types)."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            ("image/jpeg", 1 * MB, True),
            ("video/mp4", 50 * MB, True),  # exactly at limit
            ("audio/mpeg", 50 * MB + 1, False),  # 1 byte over
            ("application/pdf", 50 * MB, True),
            ("text/plain", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_slack(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [("", 1024), ("image/jpeg", 0)],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_slack(content_type, content_size)
        assert result.supported is False


class TestChannelChecksRegistry:
    """Tests for the CHANNEL_CHECKS registry."""

    def test_registry_contains_expected_channels(self):
        assert set(CHANNEL_CHECKS.keys()) == {"whatsapp", "telegram", "slack"}

    def test_registry_values_are_callable(self):
        for name, func in CHANNEL_CHECKS.items():
            result = func("image/jpeg", 1 * MB)
            assert isinstance(result, SendabilityResult), f"{name} checker returned wrong type"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_file_limits.py -v`
Expected: ImportError — `can_send_on_telegram`, `can_send_on_slack`, `CHANNEL_CHECKS` not found.

- [ ] **Step 3: Implement Telegram, Slack checkers and registry**

Add to `apps/service_providers/file_limits.py`:

```python
def can_send_on_telegram(content_type: str, content_size: int) -> SendabilityResult:
    """Telegram limits: 10MB images, 50MB audio/video/documents."""
    if not content_type or not content_size:
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        limit = 10 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB image limit for Telegram")

    if content_type.startswith(("video/", "audio/", "application/")):
        limit = 50 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        media_type = content_type.split("/")[0]
        if media_type == "application":
            media_type = "document"
        return SendabilityResult(False, f"Exceeds {limit // MB}MB {media_type} limit for Telegram")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Telegram")


def can_send_on_slack(content_type: str, content_size: int) -> SendabilityResult:
    """Slack limit: 50MB for all supported types (image/*, video/*, audio/*, application/*)."""
    if not content_type or not content_size:
        return SendabilityResult(False, "File type or size unknown")

    # Hardcoded to match settings.MAX_FILE_SIZE_MB (50). Kept as a constant here
    # rather than importing settings so this module stays pure/testable without Django.
    limit = 50 * MB
    if content_type.startswith(("image/", "video/", "audio/", "application/")):
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB file size limit for Slack")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Slack")


CHANNEL_CHECKS: dict[str, Callable[[str, int], SendabilityResult]] = {
    "whatsapp": can_send_on_whatsapp,
    "telegram": can_send_on_telegram,
    "slack": can_send_on_slack,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/service_providers/tests/test_file_limits.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py --fix
uv run ruff format apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py
git add apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py
git commit -m "feat: add Telegram, Slack checkers and CHANNEL_CHECKS registry"
```

---

### Task 3: Delegate messaging services to `file_limits.py` and delete `supported_mime_types.py`

**Files:**
- Modify: `apps/service_providers/messaging_service.py` (lines 29, 248-249, 315-329, 566-580)
- Delete: `apps/service_providers/supported_mime_types.py`
- Modify: `apps/service_providers/tests/test_messaging_providers.py`

- [ ] **Step 1: Write/update failing tests for TwilioService regression scenarios**

In `apps/service_providers/tests/test_messaging_providers.py`, add a new test class after `TestMetaCloudAPIServiceMedia`:

```python
class TestTwilioServiceCanSendFile:
    """Tests for TwilioService.can_send_file after alignment with Meta Cloud API limits."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected"),
        [
            # Basic supported types
            ("image/jpeg", 1 * 1024 * 1024, True),
            ("audio/mpeg", 1 * 1024 * 1024, True),
            ("application/pdf", 1 * 1024 * 1024, True),
            # WhatsApp image limit is 5MB (not flat 16MB)
            ("image/jpeg", 6 * 1024 * 1024, False),  # was True with old flat 16MB
            # MIME types previously rejected by allowlist, now accepted
            ("image/gif", 1 * 1024 * 1024, True),
            ("audio/ogg", 1 * 1024 * 1024, True),
            ("application/zip", 1 * 1024 * 1024, True),
            # Unsupported types still rejected
            ("text/plain", 1024, False),
            (None, 1024, False),
            ("image/jpeg", None, False),
        ],
    )
    def test_can_send_file(self, content_type, content_size, expected):
        service = TwilioService(account_sid="test", auth_token="test")
        file = MagicMock()
        file.content_type = content_type
        file.content_size = content_size
        assert service.can_send_file(file) is expected
```

You will also need to add the import for `TwilioService` at the top of the test file if not already imported. Check existing imports first.

- [ ] **Step 2: Run tests to verify the new Twilio tests fail**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestTwilioServiceCanSendFile -v`
Expected: FAIL — TwilioService still uses flat 16MB + MIME allowlist.

- [ ] **Step 3: Update `TwilioService.can_send_file()`**

In `apps/service_providers/messaging_service.py`:

Replace line 248-249:
```python
    def can_send_file(self, file: File) -> bool:
        return file.content_type in supported_mime_types.TWILIO and (file.size_mb or 0) <= self.max_file_size_mb
```

With:
```python
    def can_send_file(self, file: File) -> bool:
        from apps.service_providers.file_limits import can_send_on_whatsapp  # noqa: PLC0415

        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported
```

- [ ] **Step 4: Update `TurnIOService.can_send_file()`**

In `apps/service_providers/messaging_service.py`, replace lines 315-329:

```python
    def can_send_file(self, file: File) -> bool:
        from apps.service_providers.file_limits import can_send_on_whatsapp  # noqa: PLC0415

        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported
```

- [ ] **Step 5: Update `MetaCloudAPIService.can_send_file()`**

In `apps/service_providers/messaging_service.py`, replace lines 566-580:

```python
    def can_send_file(self, file: File) -> bool:
        from apps.service_providers.file_limits import can_send_on_whatsapp  # noqa: PLC0415

        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported
```

- [ ] **Step 6: Remove `supported_mime_types` import and delete file**

In `apps/service_providers/messaging_service.py`, remove line 29:
```python
from apps.service_providers import supported_mime_types
```

Delete the file:
```bash
rm apps/service_providers/supported_mime_types.py
```

- [ ] **Step 7: Run all messaging provider tests**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py -v`
Expected: All tests PASS (including existing `TestMetaCloudAPIServiceMedia` and new `TestTwilioServiceCanSendFile`).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py --fix
uv run ruff format apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git add apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git rm apps/service_providers/supported_mime_types.py
git commit -m "feat: delegate messaging service can_send_file to file_limits, delete supported_mime_types"
```

---

### Task 4: Delegate channel `_can_send_file()` methods to `file_limits.py`

**Files:**
- Modify: `apps/chat/channels.py` (lines 1124-1133, 1366-1371)

- [ ] **Step 1: Update `TelegramChannel._can_send_file()`**

In `apps/chat/channels.py`, replace lines 1124-1133:

```python
    def _can_send_file(self, file: File) -> bool:
        from apps.service_providers.file_limits import can_send_on_telegram  # noqa: PLC0415

        return can_send_on_telegram(file.content_type or "", file.content_size or 0).supported
```

- [ ] **Step 2: Update `SlackChannel._can_send_file()`**

In `apps/chat/channels.py`, replace lines 1366-1371:

```python
    def _can_send_file(self, file: File) -> bool:
        from apps.service_providers.file_limits import can_send_on_slack  # noqa: PLC0415

        return can_send_on_slack(file.content_type or "", file.content_size or 0).supported
```

- [ ] **Step 3: Run existing channel tests**

Run: `uv run pytest apps/channels/tests/ apps/chat/tests/ -v -k "file" --no-header`
Expected: All existing file-related tests PASS.

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check apps/chat/channels.py --fix && uv run ruff format apps/chat/channels.py
git add apps/chat/channels.py
git commit -m "feat: delegate channel _can_send_file to file_limits"
```

---

### Task 5: Add `supported_channels` field and `update_supported_channels()` to `CollectionFile`

**Files:**
- Modify: `apps/documents/models.py` (after line 57)
- Modify: `apps/utils/factories/files.py` (add `content_size` field)
- Modify: `apps/utils/factories/documents.py` (add `CollectionFileFactory`)
- Create: `apps/documents/tests/test_sendability.py`

- [ ] **Step 0: Add `content_size` to `FileFactory` and create `CollectionFileFactory`**

The `FileFactory` does not declare a `content_size` field. Since `File.content_size` is only auto-set via `File.create()` (not via normal `save()`), the factory needs it explicitly to test size-dependent logic.

In `apps/utils/factories/files.py`, add after the `content_type` line:

```python
    content_size = 1024  # default 1KB
```

In `apps/utils/factories/documents.py`, add after the `CollectionFactory` class:

```python
class CollectionFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "documents.CollectionFile"

    file = factory.SubFactory("apps.utils.factories.files.FileFactory", team=factory.SelfAttribute("..collection.team"))
    collection = factory.SubFactory(CollectionFactory)
```

- [ ] **Step 1: Write failing tests for `update_supported_channels()`**

Create `apps/documents/tests/test_sendability.py`:

```python
import pytest

from apps.documents.models import CollectionFile
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db
class TestUpdateSupportedChannels:
    """Tests for CollectionFile.update_supported_channels()."""

    def test_small_image_supported_everywhere(self):
        """A 1MB JPEG is sendable on all channels — empty dict."""
        file = FileFactory(content_type="image/jpeg", content_size=1 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert cf.supported_channels == {}

    def test_large_image_unsupported_on_whatsapp(self):
        """A 6MB image exceeds WhatsApp's 5MB limit but is fine for Telegram (10MB) and Slack (50MB)."""
        file = FileFactory(content_type="image/png", content_size=6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" not in cf.supported_channels
        assert "slack" not in cf.supported_channels
        assert cf.supported_channels["whatsapp"]["reason"]

    def test_very_large_image_unsupported_on_whatsapp_and_telegram(self):
        """An 11MB image exceeds both WhatsApp (5MB) and Telegram (10MB) limits."""
        file = FileFactory(content_type="image/jpeg", content_size=11 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" in cf.supported_channels
        assert "slack" not in cf.supported_channels

    def test_unsupported_mime_type(self):
        """A text/plain file is unsupported on all channels."""
        file = FileFactory(content_type="text/plain", content_size=1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        assert "whatsapp" in cf.supported_channels
        assert "telegram" in cf.supported_channels
        assert "slack" in cf.supported_channels

    def test_reason_format(self):
        """Reason strings should be non-empty for unsupported channels."""
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024)
        collection = CollectionFactory(is_index=False)
        cf = CollectionFile(file=file, collection=collection)
        cf.update_supported_channels()
        reason = cf.supported_channels["whatsapp"]["reason"]
        assert isinstance(reason, str)
        assert len(reason) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/documents/tests/test_sendability.py -v`
Expected: FAIL — `update_supported_channels` method does not exist.

- [ ] **Step 3: Add `supported_channels` field and `update_supported_channels()` method**

In `apps/documents/models.py`, add to `CollectionFile` class after the `external_id` field (after line 57):

```python
    supported_channels = models.JSONField(
        default=dict,
        blank=True,
        help_text="Channels that cannot send this file directly, with reasons",
    )
```

Add the method after the existing properties (after line 75):

```python
    def update_supported_channels(self):
        """Compute and set supported_channels from the file's content type and size.

        Only stores entries for unsupported channels. An empty dict means the file
        is sendable on all channels.
        """
        from apps.service_providers.file_limits import CHANNEL_CHECKS  # noqa: PLC0415

        unsupported = {}
        for channel_name, check_func in CHANNEL_CHECKS.items():
            result = check_func(self.file.content_type or "", self.file.content_size or 0)
            if not result.supported:
                unsupported[channel_name] = {"reason": result.reason}
        self.supported_channels = unsupported
```

- [ ] **Step 4: Create the migration**

Run: `uv run python manage.py makemigrations documents`
Expected: Creates a migration adding `supported_channels` JSONField to `CollectionFile`.

- [ ] **Step 5: Apply the migration**

Run: `uv run python manage.py migrate documents`

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest apps/documents/tests/test_sendability.py -v`
Expected: All tests PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check apps/documents/models.py apps/documents/tests/test_sendability.py apps/utils/factories/files.py apps/utils/factories/documents.py --fix
uv run ruff format apps/documents/models.py apps/documents/tests/test_sendability.py apps/utils/factories/files.py apps/utils/factories/documents.py
git add apps/documents/models.py apps/documents/tests/test_sendability.py apps/documents/migrations/ apps/utils/factories/files.py apps/utils/factories/documents.py
git commit -m "feat: add supported_channels field and update_supported_channels() to CollectionFile"
```

---

### Task 6: Integrate `update_supported_channels()` into file upload path

**Files:**
- Modify: `apps/documents/views.py` (lines 393-398)

- [ ] **Step 1: Update the upload view to populate sendability before `bulk_create`**

In `apps/documents/views.py`, find the `bulk_create` block (lines 393-398). Replace:

```python
        collection_files = CollectionFile.objects.bulk_create(
            [
                CollectionFile(collection=collection, file=file, status=status, metadata=metadata)
                for file in created_files
            ]
        )
```

With:

```python
        collection_file_instances = [
            CollectionFile(collection=collection, file=file, status=status, metadata=metadata)
            for file in created_files
        ]
        if not collection.is_index:
            for cf in collection_file_instances:
                cf.update_supported_channels()
        collection_files = CollectionFile.objects.bulk_create(collection_file_instances)
```

- [ ] **Step 2: Add `select_related("file")` to `collection_files_view` queryset**

In `apps/documents/views.py`, line 123, change:

```python
    collection_files = CollectionFile.objects.filter(collection=collection, document_source=document_source)
```

To:

```python
    collection_files = CollectionFile.objects.filter(
        collection=collection, document_source=document_source
    ).select_related("file")
```

- [ ] **Step 3: Run existing view tests**

Run: `uv run pytest apps/documents/tests/test_views.py -v`
Expected: All existing tests PASS.

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check apps/documents/views.py --fix && uv run ruff format apps/documents/views.py
git add apps/documents/views.py
git commit -m "feat: populate supported_channels at upload, add select_related to collection_files_view"
```

---

### Task 7: Add collection-level banner and file-level badges to templates

**Files:**
- Modify: `apps/documents/views.py` (lines 85-107 — `single_collection_home`)
- Modify: `templates/documents/single_collection_home.html`
- Modify: `templates/documents/partials/collection_files.html`

- [ ] **Step 1: Add banner query to `single_collection_home` view**

In `apps/documents/views.py`, in the `single_collection_home` function (around line 91), add after the `manually_uploaded_files_count` line:

```python
    has_unsendable_files = (
        not collection.is_index
        and CollectionFile.objects.filter(collection=collection).exclude(supported_channels={}).exists()
    )
```

Add `has_unsendable_files` to the context dict:

```python
        "has_unsendable_files": has_unsendable_files,
```

- [ ] **Step 2: Add banner to `single_collection_home.html`**

In `templates/documents/single_collection_home.html`, after the closing `</div>` of the first `app-card` div (after line 62), add:

```html
  {% if has_unsendable_files %}
    <div class="alert alert-warning shadow-sm">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <span>Some files in this collection cannot be sent directly on certain channels and will be sent as links instead. See individual file badges for details.</span>
    </div>
  {% endif %}
```

- [ ] **Step 3: Add file-level badges to `collection_files.html`**

In `templates/documents/partials/collection_files.html`, inside the `{% else %}` block for non-indexed collections (after line 43, after the summary span), add:

```html
              {% if collection_file.supported_channels %}
                <div class="flex items-center gap-1 mt-1">
                  <span class="text-xs text-base-content/60">Not supported on:</span>
                  {% for channel, info in collection_file.supported_channels.items %}
                    <div class="tooltip" data-tip="{{ info.reason }}. It will be sent as a link instead.">
                      <span class="badge badge-warning badge-sm capitalize">{{ channel }}</span>
                    </div>
                  {% endfor %}
                </div>
              {% endif %}
```

- [ ] **Step 4: Run template lint**

Run: `uv run djlint templates/documents/single_collection_home.html templates/documents/partials/collection_files.html --lint`

- [ ] **Step 5: Verify visually (manual check)**

Start the dev server and upload files of various sizes to a media collection. Check:
- Banner appears at the top when there are unsupported files
- Badges appear on files that exceed channel limits
- Tooltips show the correct reason
- No badges appear for indexed collections

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check apps/documents/views.py --fix && uv run ruff format apps/documents/views.py
git add apps/documents/views.py templates/documents/single_collection_home.html templates/documents/partials/collection_files.html
git commit -m "feat: add sendability banner and file-level channel badges to collection UI"
```

---

### Task 8: Create management command for backfilling

**Files:**
- Create: `apps/documents/management/__init__.py`
- Create: `apps/documents/management/commands/__init__.py`
- Create: `apps/documents/management/commands/populate_supported_channels.py`
- Modify: `apps/documents/tests/test_sendability.py`

- [ ] **Step 1: Write failing tests for the management command**

Append to `apps/documents/tests/test_sendability.py`:

```python
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
class TestPopulateSupportedChannelsCommand:
    """Tests for the populate_supported_channels management command."""

    def _call_command(self, *args, **kwargs):
        out = StringIO()
        call_command("populate_supported_channels", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_no_args_raises_error(self):
        with pytest.raises(CommandError):
            self._call_command()

    def test_collection_id_processes_media_collection(self):
        collection = CollectionFactory(is_index=False)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)
        assert cf.supported_channels == {}

        output = self._call_command("--collection-id", str(collection.id))

        cf.refresh_from_db()
        assert "whatsapp" in cf.supported_channels
        assert "1 files processed" in output

    def test_collection_id_skips_indexed_collection(self):
        collection = CollectionFactory(is_index=True)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024)
        CollectionFile.objects.create(file=file, collection=collection)

        output = self._call_command("--collection-id", str(collection.id))
        assert "0 files processed" in output

    def test_nonexistent_collection_id_raises_error(self):
        with pytest.raises(CommandError):
            self._call_command("--collection-id", "99999")

    def test_team_slug_processes_only_media_collections(self):
        collection_media = CollectionFactory(is_index=False)
        collection_index = CollectionFactory(is_index=True, team=collection_media.team)
        file = FileFactory(
            content_type="image/jpeg",
            content_size=6 * 1024 * 1024,
            team=collection_media.team,
        )
        cf_media = CollectionFile.objects.create(file=file, collection=collection_media)
        cf_index = CollectionFile.objects.create(file=file, collection=collection_index)

        team_slug = collection_media.team.slug
        output = self._call_command("--team", team_slug)

        cf_media.refresh_from_db()
        cf_index.refresh_from_db()
        assert "whatsapp" in cf_media.supported_channels
        assert cf_index.supported_channels == {}
        assert "1 files processed" in output

    def test_dry_run_does_not_write(self):
        collection = CollectionFactory(is_index=False)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)

        output = self._call_command("--collection-id", str(collection.id), "--dry-run")

        cf.refresh_from_db()
        assert cf.supported_channels == {}
        assert "dry run" in output.lower()

    def test_idempotent(self):
        collection = CollectionFactory(is_index=False)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024)
        cf = CollectionFile.objects.create(file=file, collection=collection)

        self._call_command("--collection-id", str(collection.id))
        cf.refresh_from_db()
        first_result = cf.supported_channels.copy()

        self._call_command("--collection-id", str(collection.id))
        cf.refresh_from_db()
        assert cf.supported_channels == first_result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/documents/tests/test_sendability.py::TestPopulateSupportedChannelsCommand -v`
Expected: FAIL — command does not exist.

- [ ] **Step 3: Create the management command**

Create the directory structure:

```bash
mkdir -p apps/documents/management/commands
touch apps/documents/management/__init__.py
touch apps/documents/management/commands/__init__.py
```

Create `apps/documents/management/commands/populate_supported_channels.py`:

```python
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Collection, CollectionFile


class Command(BaseCommand):
    help = "Populate supported_channels for files in media (non-indexed) collections."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--collection-id", type=int, help="Process a single collection by ID")
        group.add_argument("--team", type=str, help="Process all media collections for a team slug")
        parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")

    def handle(self, *args, **options):
        collection_id = options["collection_id"]
        team_slug = options["team"]
        dry_run = options["dry_run"]

        if not collection_id and not team_slug:
            raise CommandError("You must provide either --collection-id or --team.")

        if collection_id:
            try:
                collection = Collection.objects.get(id=collection_id)
            except Collection.DoesNotExist:
                raise CommandError(f"Collection with ID {collection_id} does not exist.")
            collections = Collection.objects.filter(id=collection_id, is_index=False)
        else:
            collections = Collection.objects.filter(team__slug=team_slug, is_index=False)

        files_to_process = list(
            CollectionFile.objects.filter(
                collection__in=collections
            ).select_related("file")
        )

        for cf in files_to_process:
            cf.update_supported_channels()

        total = len(files_to_process)
        unsendable_count = sum(1 for cf in files_to_process if cf.supported_channels)

        if dry_run:
            self.stdout.write(
                f"Dry run: {total} files processed, "
                f"{unsendable_count} with unsupported channels. No changes written."
            )
            return

        CollectionFile.objects.bulk_update(files_to_process, ["supported_channels"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{total} files processed, {unsendable_count} with unsupported channels."
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/documents/tests/test_sendability.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check apps/documents/management/ apps/documents/tests/test_sendability.py --fix
uv run ruff format apps/documents/management/ apps/documents/tests/test_sendability.py
git add apps/documents/management/ apps/documents/tests/test_sendability.py
git commit -m "feat: add populate_supported_channels management command"
```

---

### Task 9: View and template tests

**Files:**
- Modify: `apps/documents/tests/test_sendability.py`

- [ ] **Step 1: Write tests for banner and badge rendering**

Append to `apps/documents/tests/test_sendability.py`:

```python
from django.test import RequestFactory

from apps.documents.views import single_collection_home


@pytest.mark.django_db
class TestCollectionSendabilityUI:
    """Tests for banner and badge rendering in collection views."""

    def _get_response(self, client, collection):
        url = f"/a/{collection.team.slug}/documents/collections/{collection.id}"
        response = client.get(url)
        return response

    def test_banner_shown_when_unsendable_files_exist(self, client, team_with_users):
        collection = CollectionFactory(is_index=False, team=team_with_users)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024, team=team_with_users)
        cf = CollectionFile.objects.create(file=file, collection=collection)
        cf.update_supported_channels()
        cf.save()

        client.force_login(team_with_users.members.first())
        response = self._get_response(client, collection)
        assert response.status_code == 200
        content = response.content.decode()
        assert "cannot be sent directly" in content

    def test_banner_not_shown_when_all_files_sendable(self, client, team_with_users):
        collection = CollectionFactory(is_index=False, team=team_with_users)
        file = FileFactory(content_type="image/jpeg", content_size=1 * 1024 * 1024, team=team_with_users)
        cf = CollectionFile.objects.create(file=file, collection=collection)
        cf.update_supported_channels()
        cf.save()

        client.force_login(team_with_users.members.first())
        response = self._get_response(client, collection)
        assert response.status_code == 200
        content = response.content.decode()
        assert "cannot be sent directly" not in content

    def test_banner_not_shown_for_indexed_collection(self, client, team_with_users):
        collection = CollectionFactory(is_index=True, team=team_with_users)
        file = FileFactory(content_type="image/jpeg", content_size=6 * 1024 * 1024, team=team_with_users)
        CollectionFile.objects.create(file=file, collection=collection)

        client.force_login(team_with_users.members.first())
        response = self._get_response(client, collection)
        assert response.status_code == 200
        content = response.content.decode()
        assert "cannot be sent directly" not in content
```

Note: The exact URL pattern may need adjustment — check `apps/documents/urls.py` for the correct route. The `team_with_users` fixture is from `apps/conftest.py`. If it doesn't provide a team with users, use appropriate fixtures from the existing test suite (check `apps/documents/tests/test_views.py` for patterns).

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest apps/documents/tests/test_sendability.py::TestCollectionSendabilityUI -v`
Expected: All tests PASS.

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check apps/documents/tests/test_sendability.py --fix
uv run ruff format apps/documents/tests/test_sendability.py
git add apps/documents/tests/test_sendability.py
git commit -m "test: add view/template tests for sendability banner"
```

---

### Task 10: Final integration test and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite for affected apps**

Run: `uv run pytest apps/service_providers/tests/ apps/documents/tests/ apps/chat/tests/ apps/channels/tests/ -v --no-header`
Expected: All tests PASS.

- [ ] **Step 2: Run linting across all changed files**

```bash
uv run ruff check apps/service_providers/file_limits.py apps/service_providers/messaging_service.py apps/chat/channels.py apps/documents/models.py apps/documents/views.py apps/documents/management/ --fix
uv run ruff format apps/service_providers/file_limits.py apps/service_providers/messaging_service.py apps/chat/channels.py apps/documents/models.py apps/documents/views.py apps/documents/management/
```

- [ ] **Step 3: Run type check**

Run: `uv run ty check apps/service_providers/file_limits.py apps/documents/models.py apps/documents/management/commands/populate_supported_channels.py`

- [ ] **Step 4: Run template lint**

Run: `uv run djlint templates/documents/single_collection_home.html templates/documents/partials/collection_files.html --lint`

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -u
git commit -m "chore: lint and type-check fixes for file sendability feature"
```

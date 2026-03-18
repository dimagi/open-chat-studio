# WhatsApp Template Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable ad-hoc bot messages to be sent via WhatsApp template messages when the 24-hour customer service window has expired.

**Architecture:** The `MetaCloudAPIService` gains service window awareness. When sending a message outside the window, it automatically routes through a template message (for text) or raises `ServiceWindowExpiredException` (for voice, triggering a text fallback). A new config field lets users indicate they've set up the required template in Meta Business.

**Tech Stack:** Python, Django, pydantic, httpx, pytest

**Spec:** `docs/superpowers/specs/2026-03-18-whatsapp-template-messages-design.md`

**Future work:** Template message configuration is currently manual (users must create the `new_bot_message` template in their Meta Business account and check the config box in OCS). In the future, OCS can automate this by using the Meta WhatsApp Business Management API to create/manage message templates programmatically, removing the manual step entirely.

---

## File Structure

| File | Action | Purpose |
|------|--------|---------|
| `apps/chat/exceptions.py` | Modify | Add `ServiceWindowExpiredException` |
| `apps/service_providers/messaging_service.py` | Modify | Base class additions + `MetaCloudAPIService` template support |
| `apps/service_providers/forms.py` | Modify | Add `has_template_message_configured` to Meta config form |
| `apps/chat/channels.py` | Modify | Catch new exception, pass `last_activity_at` to messaging service |
| `apps/service_providers/tests/test_messaging_providers.py` | Modify | All unit tests for service window, template messages, splitting |
| `apps/chat/tests/test_channel_send_message.py` | Create | Channel-layer tests for exception fallback paths |

---

### Task 1: Add `ServiceWindowExpiredException`

**Files:**
- Modify: `apps/chat/exceptions.py:1-29`

- [ ] **Step 1: Write the exception class**

Add to the end of `apps/chat/exceptions.py`:

```python
class ServiceWindowExpiredException(ChatException):
    """Raised when a message cannot be sent because the messaging platform's
    service window has expired and template messages are not configured or
    the message type cannot be sent via template."""
    pass
```

- [ ] **Step 2: Verify the file is syntactically valid**

Run: `uv run python -c "from apps.chat.exceptions import ServiceWindowExpiredException; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add apps/chat/exceptions.py
git commit -m "feat: add ServiceWindowExpiredException for expired service windows"
```

---

### Task 2: Base `MessagingService` changes

**Files:**
- Modify: `apps/service_providers/messaging_service.py:34-56`

- [ ] **Step 1: Add class variable and methods to `MessagingService`**

Add `supports_template_messages` class variable and `send_template_message` method. Add `last_activity_at` to `send_text_message` and `send_voice_message` signatures.

The base class should look like this after changes:

```python
class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]
    voice_replies_supported: ClassVar[bool] = False
    supports_multimedia: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []
    supports_template_messages: ClassVar[bool] = False

    def send_text_message(
        self, message: str, from_: str, to: str, platform: ChannelPlatform, last_activity_at: datetime | None = None, **kwargs
    ):
        raise NotImplementedError

    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        raise NotImplementedError

    def send_template_message(
        self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs
    ):
        """Internal method for sending template messages. Called by send_text_message()
        when the service window is expired. Should not be called directly from channel code."""
        raise NotImplementedError

    def get_message_audio(self, message: TwilioMessage | TurnWhatsappMessage):
        """Should return a BytesIO object in .wav format"""
        raise NotImplementedError

    def resolve_number(self, number: str) -> str | None:
        """Returns `number` if the number is verified to belong to the account, otherwise return `None`"""
        return number
```

Note: `datetime` is already imported at the top of the file.

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "from apps.service_providers.messaging_service import MessagingService; print(MessagingService.supports_template_messages)"`
Expected: `False`

- [ ] **Step 3: Commit**

```bash
git add apps/service_providers/messaging_service.py
git commit -m "feat: add template message support to base MessagingService"
```

---

### Task 3: `MetaCloudAPIService` service window and template message support

**Files:**
- Modify: `apps/service_providers/messaging_service.py:335-403`
- Test: `apps/service_providers/tests/test_messaging_providers.py`

This is the core task. We'll TDD each piece.

#### 3a: `_is_within_service_window` method

- [ ] **Step 1: Write failing tests for `_is_within_service_window`**

Add to `apps/service_providers/tests/test_messaging_providers.py`:

```python
from datetime import timedelta
from django.utils import timezone


class TestMetaCloudAPIServiceWindow:
    """Tests for MetaCloudAPIService service window logic."""

    def _make_service(self, has_template=False):
        return MetaCloudAPIService(
            access_token="test_token",
            business_id="123456",
            has_template_message_configured=has_template,
        )

    def test_none_last_activity_is_outside_window(self):
        service = self._make_service()
        assert service._is_within_service_window(None) is False

    def test_23_hours_ago_is_within_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=23)
        assert service._is_within_service_window(last_activity) is True

    def test_25_hours_ago_is_outside_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=25)
        assert service._is_within_service_window(last_activity) is False

    def test_exactly_24_hours_ago_is_outside_window(self):
        service = self._make_service()
        last_activity = timezone.now() - timedelta(hours=24)
        assert service._is_within_service_window(last_activity) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v`
Expected: FAIL (no `has_template_message_configured` field, no `_is_within_service_window` method)

- [ ] **Step 3: Implement `_is_within_service_window` and add pydantic field**

In `apps/service_providers/messaging_service.py`, modify `MetaCloudAPIService`:

```python
class MetaCloudAPIService(MessagingService):
    _type: ClassVar[str] = "meta_cloud_api"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_template_messages: ClassVar[bool] = True

    access_token: str
    business_id: str
    app_secret: str = ""
    verify_token: str = ""
    has_template_message_configured: bool = False

    META_API_BASE_URL: ClassVar[str] = "https://graph.facebook.com/v25.0"
    META_API_TIMEOUT: ClassVar[int] = 30
    WHATSAPP_CHARACTER_LIMIT: ClassVar[int] = 4096
    SERVICE_WINDOW_HOURS: ClassVar[int] = 24
    TEMPLATE_MESSAGE_CHAR_LIMIT: ClassVar[int] = 974  # 1024 param limit - ~50 chars template overhead
    TEMPLATE_ELLIPSIS: ClassVar[str] = "..."

    def _is_within_service_window(self, last_activity_at: datetime | None) -> bool:
        """Check if the last user activity is within the WhatsApp 24-hour service window.

        Returns False if last_activity_at is None (no activity = outside window, require template).
        """
        if last_activity_at is None:
            return False
        return (timezone.now() - last_activity_at) < timedelta(hours=self.SERVICE_WINDOW_HOURS)
```

Note: `timedelta` is already imported at the top of the file. Add `from django.utils import timezone` to the module-level imports in `messaging_service.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git commit -m "feat: add service window check to MetaCloudAPIService"
```

#### 3b: `send_template_message` and message splitting

- [ ] **Step 6: Write failing tests for `send_template_message`**

Add to `TestMetaCloudAPIServiceWindow` class in `apps/service_providers/tests/test_messaging_providers.py`:

```python
    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_short_message(self, mock_post):
        """Template message with text under the char limit sends one request."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_template_message(
            message="Hello, any update?",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )

        mock_post.assert_called_once()
        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"
        assert data["template"]["name"] == "new_bot_message"
        assert data["template"]["language"]["code"] == "en"
        body_params = data["template"]["components"][0]["parameters"]
        assert len(body_params) == 1
        assert body_params[0]["parameter_name"] == "bot_message"
        assert body_params[0]["text"] == "Hello, any update?"

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_splits_long_message(self, mock_post):
        """Messages exceeding 974 chars are split into multiple template messages."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        # 971 chars + "..." = 974 for first chunk, remainder for second
        long_message = "A" * 1500
        service.send_template_message(
            message=long_message,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )

        assert mock_post.call_count == 2
        first_data = mock_post.call_args_list[0].kwargs["json"]
        first_text = first_data["template"]["components"][0]["parameters"][0]["text"]
        assert first_text == "A" * 971 + "..."
        assert len(first_text) == 974

        second_data = mock_post.call_args_list[1].kwargs["json"]
        second_text = second_data["template"]["components"][0]["parameters"][0]["text"]
        assert second_text == "A" * 529  # 1500 - 971 = 529

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_exactly_at_limit(self, mock_post):
        """Message exactly at 974 chars should send as one message without ellipsis."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        message = "A" * 974
        service.send_template_message(
            message=message,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )

        mock_post.assert_called_once()
        text = mock_post.call_args.kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert text == "A" * 974

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_template_message_multiple_splits(self, mock_post):
        """Very long messages produce 3+ template messages."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        # 971 * 2 = 1942 chars for first two chunks + remainders
        long_message = "B" * 2500
        service.send_template_message(
            message=long_message,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
        )

        assert mock_post.call_count == 3
        # First two chunks: 971 chars + "..."
        for i in range(2):
            text = mock_post.call_args_list[i].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
            assert text.endswith("...")
            assert len(text) == 974
        # Last chunk: remainder without ellipsis
        last_text = mock_post.call_args_list[2].kwargs["json"]["template"]["components"][0]["parameters"][0]["text"]
        assert not last_text.endswith("...")
        assert last_text == "B" * (2500 - 971 * 2)  # 558
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow::test_send_template_message_short_message -v`
Expected: FAIL (`send_template_message` not implemented)

- [ ] **Step 8: Implement `send_template_message`**

Add to `MetaCloudAPIService` in `apps/service_providers/messaging_service.py`, after `_is_within_service_window`:

```python
    def _split_template_message(self, message: str) -> list[str]:
        """Split a message into chunks that fit within the template parameter limit.

        Non-final chunks get '...' appended (so effective split point is limit - 3).
        Final chunk gets the remainder as-is.
        """
        limit = self.TEMPLATE_MESSAGE_CHAR_LIMIT
        ellipsis = self.TEMPLATE_ELLIPSIS

        if len(message) <= limit:
            return [message]

        chunks = []
        remaining = message
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            split_at = limit - len(ellipsis)
            chunks.append(remaining[:split_at] + ellipsis)
            remaining = remaining[split_at:]
        return chunks

    def send_template_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        """Send a WhatsApp template message using the 'new_bot_message' template.

        This is an internal method called by send_text_message() when the service window
        is expired. It should not be called directly from channel code.
        """
        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        chunks = self._split_template_message(message)

        for chunk in chunks:
            data = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": {
                    "name": "new_bot_message",
                    "language": {
                        "code": "en",
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "parameter_name": "bot_message",
                                    "text": chunk,
                                }
                            ],
                        }
                    ],
                },
            }
            response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
            response.raise_for_status()
```

- [ ] **Step 9: Run all template message tests**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v -k "template"  `
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git commit -m "feat: implement send_template_message with message splitting on MetaCloudAPIService"
```

#### 3c: `send_text_message` with service window routing

- [ ] **Step 11: Write failing tests for `send_text_message` routing**

Add to `TestMetaCloudAPIServiceWindow` class:

```python
    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_within_window_sends_normal(self, mock_post):
        """Text message within service window sends normally."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        last_activity = timezone.now() - timedelta(hours=1)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=last_activity,
        )

        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "text"

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_outside_window_with_template_sends_template(self, mock_post):
        """Text message outside service window routes to template when configured."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        last_activity = timezone.now() - timedelta(hours=25)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=last_activity,
        )

        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"

    def test_send_text_outside_window_without_template_raises(self):
        """Text message outside window with no template configured raises ServiceWindowExpiredException."""
        from apps.chat.exceptions import ServiceWindowExpiredException

        service = self._make_service(has_template=False)
        last_activity = timezone.now() - timedelta(hours=25)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_text_message(
                message="Hello",
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=last_activity,
            )

    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_text_none_activity_with_template_sends_template(self, mock_post):
        """Text message with None last_activity_at routes to template when configured."""
        mock_post.return_value = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        service = self._make_service(has_template=True)
        service.send_text_message(
            message="Hello",
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=None,
        )

        data = mock_post.call_args.kwargs["json"]
        assert data["type"] == "template"

    def test_send_text_none_activity_without_template_raises(self):
        """Text message with None last_activity_at and no template raises ServiceWindowExpiredException."""
        from apps.chat.exceptions import ServiceWindowExpiredException

        service = self._make_service(has_template=False)
        with pytest.raises(ServiceWindowExpiredException):
            service.send_text_message(
                message="Hello",
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=None,
            )
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v -k "send_text"`
Expected: FAIL

- [ ] **Step 13: Implement `send_text_message` override**

Also add `from apps.chat.exceptions import ServiceWindowExpiredException` to the module-level imports in `messaging_service.py`.

Replace the existing `send_text_message` in `MetaCloudAPIService` with:

```python
    def send_text_message(
        self, message: str, from_: str, to: str, platform: ChannelPlatform, last_activity_at: datetime | None = None, **kwargs
    ):
        if not self._is_within_service_window(last_activity_at):
            if self.has_template_message_configured:
                logger.info("Service window expired, sending template message instead of text")
                return self.send_template_message(message=message, from_=from_, to=to, platform=platform)
            logger.warning("Service window expired and template message not configured, cannot send message")
            raise ServiceWindowExpiredException("Service window expired and template message not configured")

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        chunks = smart_split(message, chars_per_string=self.WHATSAPP_CHARACTER_LIMIT)
        for chunk in chunks:
            data = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk},
            }
            response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
            response.raise_for_status()
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v -k "send_text"`
Expected: PASS

- [ ] **Step 15: Commit**

```bash
git add apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git commit -m "feat: route text messages through template when service window expired"
```

#### 3d: `send_voice_message` with service window check

- [ ] **Step 16: Write failing tests for `send_voice_message`**

Add to `TestMetaCloudAPIServiceWindow` class:

```python
    @patch("apps.service_providers.messaging_service.httpx.post")
    def test_send_voice_within_window_sends_normal(self, mock_post):
        """Voice message within service window sends normally."""
        upload_response = httpx.Response(
            200,
            json={"id": "media_id_abc"},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/media"),
        )
        send_response = httpx.Response(
            200,
            json={"messages": [{"id": "wamid.xyz"}]},
            request=httpx.Request("POST", "https://graph.facebook.com/v25.0/phone123/messages"),
        )
        mock_post.side_effect = [upload_response, send_response]

        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        synthetic_voice.get_audio_bytes.return_value = b"fake-ogg-audio"

        last_activity = timezone.now() - timedelta(hours=1)
        service.send_voice_message(
            synthetic_voice=synthetic_voice,
            from_="phone123",
            to="+27826419977",
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=last_activity,
        )

        assert mock_post.call_count == 2

    def test_send_voice_outside_window_raises_regardless_of_template(self):
        """Voice message outside service window always raises, even if template is configured."""
        from apps.chat.exceptions import ServiceWindowExpiredException

        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)
        last_activity = timezone.now() - timedelta(hours=25)

        with pytest.raises(ServiceWindowExpiredException):
            service.send_voice_message(
                synthetic_voice=synthetic_voice,
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=last_activity,
            )

        # Verify no audio bytes were processed (check happens before upload)
        synthetic_voice.get_audio_bytes.assert_not_called()

    def test_send_voice_none_activity_raises(self):
        """Voice message with None last_activity_at raises ServiceWindowExpiredException."""
        from apps.chat.exceptions import ServiceWindowExpiredException

        service = self._make_service(has_template=True)
        synthetic_voice = MagicMock(spec=SynthesizedAudio)

        with pytest.raises(ServiceWindowExpiredException):
            service.send_voice_message(
                synthetic_voice=synthetic_voice,
                from_="phone123",
                to="+27826419977",
                platform=ChannelPlatform.WHATSAPP,
                last_activity_at=None,
            )

        synthetic_voice.get_audio_bytes.assert_not_called()
```

- [ ] **Step 17: Run tests to verify they fail**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v -k "send_voice"`
Expected: FAIL

- [ ] **Step 18: Implement `send_voice_message` override**

Replace the existing `send_voice_message` in `MetaCloudAPIService` with:

```python
    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        if not self._is_within_service_window(last_activity_at):
            logger.info("Service window expired, cannot send voice message via template")
            raise ServiceWindowExpiredException("Service window expired, voice messages cannot be sent via template")

        voice_audio_bytes = synthetic_voice.get_audio_bytes(format="ogg", codec="libopus")
        media_id = self._upload_media(from_, voice_audio_bytes, mime_type="audio/ogg")

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        }
        response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
        response.raise_for_status()
```

- [ ] **Step 19: Run all Task 3 tests**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py::TestMetaCloudAPIServiceWindow -v`
Expected: ALL PASS

- [ ] **Step 20: Fix existing tests that will break due to service window check**

The existing `test_send_voice_message` test (line 309 of `test_messaging_providers.py`) calls `send_voice_message` without `last_activity_at`, which defaults to `None`. With `None`, `_is_within_service_window` returns `False`, and the test will now raise `ServiceWindowExpiredException`.

Fix by adding `last_activity_at=timezone.now()` to the existing test's call:

```python
    meta_cloud_api_service.send_voice_message(
        synthetic_voice=synthetic_voice,
        from_="phone123",
        to="27826419977",
        platform=ChannelPlatform.WHATSAPP,
        last_activity_at=timezone.now(),
    )
```

Add the required import at the top of the test file if not already present:
```python
from django.utils import timezone
```

- [ ] **Step 21: Run ALL existing messaging provider tests to check for regressions**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py -v`
Expected: ALL PASS

- [ ] **Step 22: Commit**

```bash
git add apps/service_providers/messaging_service.py apps/service_providers/tests/test_messaging_providers.py
git commit -m "feat: add service window check to send_voice_message on MetaCloudAPIService"
```

---

### Task 4: Config form for `has_template_message_configured`

**Files:**
- Modify: `apps/service_providers/forms.py:259-280`

- [ ] **Step 1: Add the boolean field to `MetaCloudAPIMessagingConfigForm`**

Add the field after the `verify_token` field in `MetaCloudAPIMessagingConfigForm`:

```python
    has_template_message_configured = forms.BooleanField(
        label=_("I have configured the 'new_bot_message' template in my Meta Business account"),
        required=False,
        initial=False,
    )
```

- [ ] **Step 2: Verify the form works**

Run: `uv run python -c "
from apps.service_providers.forms import MetaCloudAPIMessagingConfigForm
form = MetaCloudAPIMessagingConfigForm(None, data={
    'business_id': '123',
    'access_token': 'token',
    'app_secret': 'secret',
    'verify_token': 'verify',
    'has_template_message_configured': True,
})
print('valid:', form.is_valid())
print('template:', form.cleaned_data.get('has_template_message_configured'))
"`
Expected:
```
valid: True
template: True
```

- [ ] **Step 3: Verify it passes through to service construction**

Run: `uv run python -c "
from apps.service_providers.models import MessagingProviderType
service = MessagingProviderType.meta_cloud_api.get_messaging_service({
    'access_token': 'test',
    'business_id': '123',
    'has_template_message_configured': True,
})
print('has_template:', service.has_template_message_configured)
"`
Expected: `has_template: True`

- [ ] **Step 4: Commit**

```bash
git add apps/service_providers/forms.py
git commit -m "feat: add template message config checkbox to Meta Cloud API provider form"
```

---

### Task 5: Channel layer changes

**Files:**
- Modify: `apps/chat/channels.py:546-561` (exception catch block)
- Modify: `apps/chat/channels.py:1174-1186` (WhatsappChannel send methods)
- Modify: `apps/chat/channels.py:1202-1207` (SureAdhereChannel)
- Modify: `apps/chat/channels.py:1215-1237` (FacebookMessengerChannel)
- Modify: `apps/chat/channels.py:1315-1327` (SlackChannel)
- Test: `apps/chat/tests/test_channel_send_message.py` (new file)

#### 5a: Add exception import and expand catch block

- [ ] **Step 1: Add `ServiceWindowExpiredException` import**

In `apps/chat/channels.py`, find the imports from `apps.chat.exceptions` (around line 28-32) and add `ServiceWindowExpiredException`:

```python
from apps.chat.exceptions import (
    AudioSynthesizeException,
    ChannelException,
    ChatException,
    ParticipantNotAllowedException,
    ServiceWindowExpiredException,
    VersionedExperimentSessionsNotAllowedException,
)
```

- [ ] **Step 2: Expand the `except` block in `send_message_to_user`**

In `apps/chat/channels.py`, change the except block at line 556 from:

```python
            except AudioSynthesizeException:
                logger.exception("Error generating voice response")
                audio_synthesis_failure_notification(self.experiment, session=self.experiment_session)
                self._bot_message_is_voice = False
                bot_message = f"{bot_message}\n\n{urls_to_append}"
                self._send_text_to_user_with_notification(bot_message)
```

to:

```python
            except (AudioSynthesizeException, ServiceWindowExpiredException) as exc:
                if isinstance(exc, AudioSynthesizeException):
                    logger.exception("Error generating voice response")
                    audio_synthesis_failure_notification(self.experiment, session=self.experiment_session)
                else:
                    logger.info("Service window expired, falling back to text message")
                self._bot_message_is_voice = False
                bot_message = f"{bot_message}\n\n{urls_to_append}"
                self._send_text_to_user_with_notification(bot_message)
```

- [ ] **Step 3: Verify syntax**

Run: `uv run python -c "from apps.chat.channels import ChannelBase; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add apps/chat/channels.py
git commit -m "feat: catch ServiceWindowExpiredException in voice fallback path"
```

#### 5b: Pass `last_activity_at` from all channel implementations

- [ ] **Step 5: Update `WhatsappChannel.send_text_to_user`**

Change from:
```python
    def send_text_to_user(self, text: str):
        self.messaging_service.send_text_message(
            message=text, from_=self.from_identifier, to=self.participant_identifier, platform=ChannelPlatform.WHATSAPP
        )
```
to:
```python
    def send_text_to_user(self, text: str):
        self.messaging_service.send_text_message(
            message=text,
            from_=self.from_identifier,
            to=self.participant_identifier,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 6: Update `WhatsappChannel.send_voice_to_user`**

Change from:
```python
    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """Uploads the synthesized voice to AWS and sends the public link to the messaging provider."""
        self.messaging_service.send_voice_message(
            synthetic_voice,
            from_=self.from_identifier,
            to=self.participant_identifier,
            platform=ChannelPlatform.WHATSAPP,
        )
```
to:
```python
    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """Uploads the synthesized voice to AWS and sends the public link to the messaging provider."""
        self.messaging_service.send_voice_message(
            synthetic_voice,
            from_=self.from_identifier,
            to=self.participant_identifier,
            platform=ChannelPlatform.WHATSAPP,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 7: Update `SureAdhereChannel.send_text_to_user`**

Change from:
```python
    def send_text_to_user(self, text: str):
        from_ = self.experiment_channel.extra_data.get("sureadhere_tenant_id")
        to_patient = self.participant_identifier
        self.messaging_service.send_text_message(
            message=text, from_=from_, to=to_patient, platform=ChannelPlatform.SUREADHERE
        )
```
to:
```python
    def send_text_to_user(self, text: str):
        from_ = self.experiment_channel.extra_data.get("sureadhere_tenant_id")
        to_patient = self.participant_identifier
        self.messaging_service.send_text_message(
            message=text,
            from_=from_,
            to=to_patient,
            platform=ChannelPlatform.SUREADHERE,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 8: Update `FacebookMessengerChannel.send_text_to_user`**

Change from:
```python
    def send_text_to_user(self, text: str):
        from_ = self.experiment_channel.extra_data.get("page_id")
        self.messaging_service.send_text_message(
            message=text, from_=from_, to=self.participant_identifier, platform=ChannelPlatform.FACEBOOK
        )
```
to:
```python
    def send_text_to_user(self, text: str):
        from_ = self.experiment_channel.extra_data.get("page_id")
        self.messaging_service.send_text_message(
            message=text,
            from_=from_,
            to=self.participant_identifier,
            platform=ChannelPlatform.FACEBOOK,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 9: Update `FacebookMessengerChannel.send_voice_to_user`**

Change from:
```python
    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """Uploads the synthesized voice to AWS and sends the public link to the messaging provider."""
        from_ = self.experiment_channel.extra_data["page_id"]
        self.messaging_service.send_voice_message(
            synthetic_voice, from_=from_, to=self.participant_identifier, platform=ChannelPlatform.FACEBOOK
        )
```
to:
```python
    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """Uploads the synthesized voice to AWS and sends the public link to the messaging provider."""
        from_ = self.experiment_channel.extra_data["page_id"]
        self.messaging_service.send_voice_message(
            synthetic_voice,
            from_=from_,
            to=self.participant_identifier,
            platform=ChannelPlatform.FACEBOOK,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 10: Update `SlackChannel.send_text_to_user`**

Change from:
```python
    def send_text_to_user(self, text: str):
        if not self.message:
            channel_id, thread_ts = parse_session_external_id(self.experiment_session.external_id)
        else:
            channel_id = self.message.channel_id
            thread_ts = self.message.thread_ts
        self.messaging_service.send_text_message(
            text,
            from_="",
            to=channel_id,
            platform=ChannelPlatform.SLACK,
            thread_ts=thread_ts,
        )
```
to:
```python
    def send_text_to_user(self, text: str):
        if not self.message:
            channel_id, thread_ts = parse_session_external_id(self.experiment_session.external_id)
        else:
            channel_id = self.message.channel_id
            thread_ts = self.message.thread_ts
        self.messaging_service.send_text_message(
            text,
            from_="",
            to=channel_id,
            platform=ChannelPlatform.SLACK,
            thread_ts=thread_ts,
            last_activity_at=self.experiment_session.last_activity_at if self.experiment_session else None,
        )
```

- [ ] **Step 11: Verify syntax**

Run: `uv run python -c "from apps.chat.channels import WhatsappChannel, SlackChannel, FacebookMessengerChannel, SureAdhereChannel; print('OK')"`
Expected: `OK`

- [ ] **Step 12: Commit**

```bash
git add apps/chat/channels.py
git commit -m "feat: pass last_activity_at to messaging service from all channel implementations"
```

#### 5c: Channel-layer tests

- [ ] **Step 13: Write channel-layer tests**

Create `apps/chat/tests/test_channel_send_message.py`:

```python
from unittest.mock import MagicMock, patch

import pytest

from apps.chat.exceptions import AudioSynthesizeException, ServiceWindowExpiredException


class TestSendMessageToUserVoiceFallback:
    """Tests for ChannelBase.send_message_to_user voice fallback behavior."""

    def _make_channel(self, voice_enabled=True):
        """Create a mock channel with the send_message_to_user method from ChannelBase."""
        from apps.chat.channels import ChannelBase

        channel = MagicMock(spec=ChannelBase)
        # Only bind the method under test; leave _reply_voice_message as a MagicMock
        # so .side_effect works correctly in tests
        channel.send_message_to_user = ChannelBase.send_message_to_user.__get__(channel)
        channel._format_reference_section = MagicMock(return_value=("test message", []))
        channel.append_attachment_links = MagicMock(side_effect=lambda msg, **kw: msg)
        channel._get_supported_unsupported_files = MagicMock(return_value=([], []))
        channel.supports_multimedia = False
        channel.message = None
        channel._bot_message_is_voice = False

        # Configure voice support
        channel.voice_replies_supported = voice_enabled
        if voice_enabled:
            channel.experiment = MagicMock()
            channel.experiment.synthetic_voice = MagicMock()
            channel.experiment.voice_response_behaviour = "always"

        return channel

    def test_voice_service_window_expired_falls_back_to_text(self):
        """When voice message raises ServiceWindowExpiredException, falls back to text."""
        channel = self._make_channel(voice_enabled=True)
        channel._reply_voice_message.side_effect = ServiceWindowExpiredException("window expired")
        channel._send_text_to_user_with_notification = MagicMock()

        channel.send_message_to_user("Hello")

        channel._send_text_to_user_with_notification.assert_called_once()
        assert channel._bot_message_is_voice is False

    def test_voice_audio_synthesize_error_falls_back_to_text(self):
        """Existing behavior: AudioSynthesizeException falls back to text."""
        channel = self._make_channel(voice_enabled=True)
        channel._reply_voice_message.side_effect = AudioSynthesizeException("synth failed")
        channel._send_text_to_user_with_notification = MagicMock()
        channel.experiment_session = MagicMock()

        channel.send_message_to_user("Hello")

        channel._send_text_to_user_with_notification.assert_called_once()
        assert channel._bot_message_is_voice is False

    def test_text_service_window_expired_propagates(self):
        """When text message raises ServiceWindowExpiredException (no template), it propagates up."""
        channel = self._make_channel(voice_enabled=False)
        channel._send_text_to_user_with_notification = MagicMock(
            side_effect=ServiceWindowExpiredException("no template configured")
        )

        with pytest.raises(ServiceWindowExpiredException):
            channel.send_message_to_user("Hello")
```

- [ ] **Step 14: Run channel tests**

Run: `uv run pytest apps/chat/tests/test_channel_send_message.py -v`
Expected: PASS

- [ ] **Step 15: Run the full existing test suites to check for regressions**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py apps/channels/tests/ -v`
Expected: ALL PASS

- [ ] **Step 16: Commit**

```bash
git add apps/chat/tests/test_channel_send_message.py
git commit -m "test: add channel-layer tests for ServiceWindowExpiredException fallback paths"
```

---

### Task 6: Lint, format, and type-check

**Files:**
- All modified files

- [ ] **Step 1: Lint all modified files**

Run:
```bash
uv run ruff check apps/chat/exceptions.py apps/service_providers/messaging_service.py apps/service_providers/forms.py apps/chat/channels.py apps/service_providers/tests/test_messaging_providers.py apps/chat/tests/test_channel_send_message.py --fix
```
Expected: No errors (or auto-fixed)

- [ ] **Step 2: Format all modified files**

Run:
```bash
uv run ruff format apps/chat/exceptions.py apps/service_providers/messaging_service.py apps/service_providers/forms.py apps/chat/channels.py apps/service_providers/tests/test_messaging_providers.py apps/chat/tests/test_channel_send_message.py
```

- [ ] **Step 3: Type-check**

Run: `uv run ty check apps/`
Expected: No errors

- [ ] **Step 4: Run ALL tests one final time**

Run: `uv run pytest apps/service_providers/tests/test_messaging_providers.py apps/chat/tests/test_channel_send_message.py apps/channels/tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit if any formatting changes were made**

```bash
git add -u
git commit -m "style: lint and format WhatsApp template message changes"
```

# WhatsApp Template Messages for Ad-Hoc Bot Messages

## Problem

Bots can send ad-hoc messages to users, but WhatsApp enforces a 24-hour customer service window. Outside this window, only pre-approved template messages can be sent. We need the Meta Cloud API messaging service to automatically route messages through templates when the service window has expired.

## Design

### Overview

The `MetaCloudAPIService` gains awareness of the WhatsApp service window. When sending a message outside the 24-hour window, it automatically uses a template message instead of a regular text message. For voice messages outside the window, it raises an exception so the channel layer falls back to sending text (which then routes through the template).

A new boolean field on `MessagingProvider` lets users indicate whether they have configured the required template in their Meta Business account.

### 1. Base MessagingService Changes

**File:** `apps/service_providers/messaging_service.py`

- Add class variable: `supports_template_messages: ClassVar[bool] = False`
- Add method: `send_template_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs)` — raises `NotImplementedError` by default
- Add optional parameter `last_activity_at: datetime | None = None` to `send_text_message()` and `send_voice_message()` signatures

**New exception:** `ServiceWindowExpiredException` — raised when a message cannot be sent because the service window has expired and either template messages are not configured or the message type (voice) cannot be sent via template.

### 2. MetaCloudAPIService Changes

**File:** `apps/service_providers/messaging_service.py`

**New field:** `has_template_message_configured: bool = False` — populated from MessagingProvider config.

**Private method:** `_is_within_service_window(last_activity_at: datetime | None) -> bool`
- `last_activity_at is None` → returns `False` (no activity = outside window, require template)
- `last_activity_at` within 24 hours of `timezone.now()` → returns `True`
- Otherwise → returns `False`

**`send_text_message()` override:**
- If within service window → send normal text message (existing behavior)
- If outside window AND `has_template_message_configured` is `True` → call `send_template_message()`
- If outside window AND `has_template_message_configured` is `False` → raise `ServiceWindowExpiredException`

**`send_voice_message()` override:**
- If within service window → send normal voice message (existing behavior)
- If outside window → raise `ServiceWindowExpiredException` (regardless of template config, since templates only support text)

**`send_template_message()` implementation:**
- Sends the Meta Cloud API template payload:
  - Template name: `"new_bot_message"`
  - Language code: `"en"`
  - Single named body parameter: `"bot_message"` containing the message text
- Character limit: Template has ~50 characters of overhead. The body parameter limit is 1024 chars, so the effective bot message limit per template is **974 characters**.
- If the message exceeds 974 chars, split into multiple template messages. First N-1 messages get `...` appended. Last message gets the remainder.

**Template message payload structure:**
```json
{
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": "<phone_number>",
    "type": "template",
    "template": {
        "name": "new_bot_message",
        "language": {
            "code": "en"
        },
        "components": [
            {
                "type": "body",
                "parameters": [
                    {
                        "type": "text",
                        "parameter_name": "bot_message",
                        "text": "<message_text>"
                    }
                ]
            }
        ]
    }
}
```

### 3. MessagingProvider Model Changes

**File:** `apps/service_providers/models.py`

Add a boolean field to `MessagingProvider`: `has_template_message_configured` (default `False`). This field is relevant only for `meta_cloud_api` type providers.

This value is passed into the `MetaCloudAPIService` constructor via the config dict when `get_messaging_service()` is called.

The UI for configuring a Meta Cloud API provider includes a checkbox: "I have configured the `new_bot_message` template in my Meta Business account."

### 4. Channel Layer Changes

**File:** `apps/chat/channels.py`

**`ChannelBase.send_message_to_user()`:** The `except AudioSynthesizeException` block is expanded to also catch `ServiceWindowExpiredException`. Same fallback behavior: log, flip `_bot_message_is_voice` to False, send as text.

**Passing `last_activity_at`:** All channel implementations that have access to an `experiment_session` pass `self.experiment_session.last_activity_at` to `send_text_message()` and `send_voice_message()`. This keeps interfaces aligned across channels — services that don't care about it simply ignore the parameter.

### 5. Decision Logic Summary

| Condition | Text Message | Voice Message |
|-----------|-------------|---------------|
| Within 24h window | Normal text | Normal voice |
| Outside window + template configured | Template message | Raise `ServiceWindowExpiredException` → fallback to text → template |
| Outside window + template NOT configured | Raise `ServiceWindowExpiredException` | Raise `ServiceWindowExpiredException` |
| `last_activity_at` is None + template configured | Template message | Raise `ServiceWindowExpiredException` → fallback to text → template |
| `last_activity_at` is None + template NOT configured | Raise `ServiceWindowExpiredException` | Raise `ServiceWindowExpiredException` |

### 6. Logging

- **INFO** level when falling back to template message (text or voice→text)
- **WARNING** level when failing due to expired window and no template configured

### 7. Testing

- Unit test `_is_within_service_window` with: None, 23 hours ago, 25 hours ago, exactly 24 hours ago
- Unit test `send_text_message` routes to template when outside window + template configured
- Unit test `send_text_message` raises `ServiceWindowExpiredException` when outside window + template NOT configured
- Unit test `send_voice_message` raises `ServiceWindowExpiredException` when outside window (regardless of template config)
- Unit test message splitting: under 974 chars, exactly 974, over 974, multiple splits needed
- Unit test `ChannelBase.send_message_to_user` catches `ServiceWindowExpiredException` and falls back to text
- Integration test: voice outside window → exception → fallback to text → template message sent

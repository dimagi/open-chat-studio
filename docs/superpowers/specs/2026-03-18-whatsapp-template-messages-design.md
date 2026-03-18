# WhatsApp Template Messages for Ad-Hoc Bot Messages

## Problem

Bots can send ad-hoc messages to users, but WhatsApp enforces a 24-hour customer service window. Outside this window, only pre-approved template messages can be sent. We need the Meta Cloud API messaging service to automatically route messages through templates when the service window has expired.

## Design

### Overview

The `MetaCloudAPIService` gains awareness of the WhatsApp service window. When sending a message outside the 24-hour window, it automatically uses a template message instead of a regular text message. For voice messages outside the window, it raises an exception so the channel layer falls back to sending text (which then routes through the template).

A new boolean config field on the Meta Cloud API messaging provider form lets users indicate whether they have configured the required template in their Meta Business account.

### 1. Base MessagingService Changes

**File:** `apps/service_providers/messaging_service.py`

- Add class variable: `supports_template_messages: ClassVar[bool] = False`
- Add method: `send_template_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs)` — raises `NotImplementedError` by default. This is an internal method called by `send_text_message()` when the service window is expired. It should not be called directly from channel code.
- Add optional parameter `last_activity_at: datetime | None = None` to `send_text_message()` and `send_voice_message()` signatures

**New exception:** `ServiceWindowExpiredException` in `apps/chat/exceptions.py` — raised when a message cannot be sent because the service window has expired and either template messages are not configured or the message type (voice) cannot be sent via template.

### 2. MetaCloudAPIService Changes

**File:** `apps/service_providers/messaging_service.py`

**New pydantic field:** `has_template_message_configured: bool = False` — populated from the MessagingProvider config dict (passed via `**config`).

**Private method:** `_is_within_service_window(last_activity_at: datetime | None) -> bool`
- `last_activity_at is None` → returns `False` (no activity = outside window, require template)
- `last_activity_at` within 24 hours of `timezone.now()` → returns `True`
- Otherwise → returns `False`

**`send_text_message()` override:**
- If within service window → send normal text message (existing behavior)
- If outside window AND `has_template_message_configured` is `True` → call `send_template_message()`
- If outside window AND `has_template_message_configured` is `False` → raise `ServiceWindowExpiredException`

**`send_voice_message()` override:**
- Check the service window **before** doing any audio upload work
- If within service window → send normal voice message (existing behavior)
- If outside window → raise `ServiceWindowExpiredException` (regardless of template config, since templates only support text)

**`send_template_message()` implementation:**
- Sends the Meta Cloud API template payload:
  - Template name: `"new_bot_message"`
  - Language code: `"en"` (hardcoded for v1; internationalization is out of scope)
  - Single named body parameter: `"bot_message"` containing the message text
- Character limit: Template has ~50 characters of overhead. The body parameter limit is 1024 chars, so the effective bot message limit per template is **974 characters**. The 50-char overhead is an approximation that should be validated against the actual registered template.
- If the message exceeds 974 chars, split into multiple template messages. First N-1 messages get `...` appended (3 chars, so effective split point is 971 chars for non-final chunks). Last message gets the remainder.
- Error handling: calls `response.raise_for_status()` like existing send methods. No special handling for template-specific error codes in v1.

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

### 3. MessagingProvider Config Form Changes

**File:** `apps/service_providers/forms.py`

Add a `BooleanField` to `MetaCloudAPIMessagingConfigForm`: `has_template_message_configured` (default `False`). Label: "I have configured the `new_bot_message` template in my Meta Business account."

This value is stored in the encrypted `config` JSON dict on `MessagingProvider` and passed to `MetaCloudAPIService` via `**config` in `get_messaging_service()`. No model migration is needed — it's a config dict entry, not a Django model field.

### 4. Channel Layer Changes

**File:** `apps/chat/channels.py`

**Exception propagation path for voice:** The call chain is:
```
send_message_to_user()
  → _reply_voice_message()
    → _send_voice_to_user_with_notification()  [has @notify_on_delivery_failure]
      → send_voice_to_user()
        → messaging_service.send_voice_message()  [raises ServiceWindowExpiredException]
```
The `@notify_on_delivery_failure` decorator catches, logs, creates a notification, and **re-raises** the exception. So `ServiceWindowExpiredException` propagates up to the `except` block in `send_message_to_user()`. This is the desired behavior — the notification serves as an alert, and the fallback to text still happens.

**Exception propagation path for text (when template not configured):** The call chain is:
```
send_message_to_user()
  → _send_text_to_user_with_notification()  [has @notify_on_delivery_failure]
    → send_text_to_user()
      → messaging_service.send_text_message()  [raises ServiceWindowExpiredException]
```
When `send_text_message()` raises `ServiceWindowExpiredException` because the template is not configured, the `@notify_on_delivery_failure` decorator catches it, logs it, creates a delivery failure notification, and re-raises. Since there is no catch for this in the text branch of `send_message_to_user()`, it propagates up to the caller (e.g., `ad_hoc_bot_message()` which has `fail_silently=True` by default). This is acceptable — if the user hasn't configured templates and the window is expired, the message genuinely cannot be delivered, and a delivery failure notification is the correct outcome.

**`ChannelBase.send_message_to_user()`:** The `except AudioSynthesizeException` block (line 556) is expanded to also catch `ServiceWindowExpiredException`. Same fallback behavior: log, flip `_bot_message_is_voice` to False, send as text. This catch only applies in the voice branch — the text fallback will then succeed (via template) or fail (if template not configured, handled as described above).

**Passing `last_activity_at`:** All channel implementations that have access to an `experiment_session` pass `self.experiment_session.last_activity_at` to `send_text_message()` and `send_voice_message()`. This keeps interfaces aligned across channels — services that don't care about it simply ignore the parameter via `**kwargs`.

### 5. Decision Logic Summary

| Condition | Text Message | Voice Message |
|-----------|-------------|---------------|
| Within 24h window | Normal text | Normal voice |
| Outside window + template configured | Template message | Raise `ServiceWindowExpiredException` → fallback to text → template |
| Outside window + template NOT configured | Raise `ServiceWindowExpiredException` (delivery failure notification) | Raise `ServiceWindowExpiredException` → fallback to text → raise again (delivery failure notification) |
| `last_activity_at` is None + template configured | Template message | Raise `ServiceWindowExpiredException` → fallback to text → template |
| `last_activity_at` is None + template NOT configured | Raise `ServiceWindowExpiredException` (delivery failure notification) | Raise `ServiceWindowExpiredException` → fallback to text → raise again (delivery failure notification) |

### 6. Scope

**In scope:** `MetaCloudAPIService` only.

**Out of scope:** `TurnIOService` (also WhatsApp, but Turn.io may handle templating at their layer — can be added later following the same pattern). Language internationalization for templates (hardcoded to `"en"` for v1).

**Future work:** Template message configuration is currently manual (users must create the `new_bot_message` template in their Meta Business account and check the config box in OCS). In the future, OCS can automate this by using the Meta WhatsApp Business Management API to create/manage message templates programmatically, removing the manual step entirely.

### 7. Assumptions

- Consent and survey messages also flow through `send_text_message()` and will hit the service window check. This is acceptable because WhatsApp requires user-initiated contact to open a conversation, so these messages always occur in response to a user message (i.e., within the 24-hour service window).

### 8. Logging

- **INFO** level when falling back to template message (text or voice→text)
- **WARNING** level when failing due to expired window and no template configured

### 9. Testing

- Unit test `_is_within_service_window` with: None, 23 hours ago, 25 hours ago, exactly 24 hours ago
- Unit test `send_text_message` routes to template when outside window + template configured
- Unit test `send_text_message` raises `ServiceWindowExpiredException` when outside window + template NOT configured
- Unit test `send_voice_message` raises `ServiceWindowExpiredException` when outside window (regardless of template config)
- Unit test message splitting: under 974 chars, exactly 974, over 974 (single split), multiple splits needed, accounting for `...` (3-char) overhead on non-final chunks
- Unit test `ChannelBase.send_message_to_user` catches `ServiceWindowExpiredException` and falls back to text
- Integration test: voice outside window → exception → `@notify_on_delivery_failure` fires and re-raises → fallback to text → template message sent
- Test that `@notify_on_delivery_failure` does not swallow `ServiceWindowExpiredException`
- Unit test `ChannelBase.send_message_to_user` in **text mode** (not voice fallback): verify `ServiceWindowExpiredException` propagates up to caller when template is not configured and window is expired, and that `@notify_on_delivery_failure` fires before propagation

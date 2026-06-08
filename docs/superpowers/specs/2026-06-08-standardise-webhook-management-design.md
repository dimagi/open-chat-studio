# Standardise webhook management across channels

**Date:** 2026-06-08
**Status:** approved

## Problem

Two messaging platforms configure their inbound webhook in two different places:

- **Provider-backed platforms** (Twilio/WhatsApp) configure the webhook through
  `MessagingService.set_incoming_webhook()`, dispatched via
  `channel.messaging_provider.get_messaging_service()`.
- **Telegram** configures its webhook directly in the channel form
  (`TelegramChannelForm._set_telegram_webhook`, using `telebot.TeleBot.set_webhook`).

Telegram has no `MessagingProvider` because a `MessagingProvider` models *team-scoped,
shared* credentials (one Twilio account / Slack workspace reused across many channels),
whereas a Telegram bot token is *per-channel* (1:1) and lives in
`ExperimentChannel.extra_data["bot_token"]`. There is nothing team-level to share, so a
"Telegram messaging provider" would be a semantically empty record. That distinction is
legitimate and is **not** being changed.

The real problem is that the webhook-management *capability*
(`supports_webhook_management`, `set_incoming_webhook`, `remove_incoming_webhook`) is
coupled to `MessagingService` only because that is where Twilio's API client happens to
live. The capability is orthogonal to credential ownership.

### Latent bug this divergence hides

On channel delete, `apps/channels/views.py::_clear_remote_webhook` only runs when the
channel has a `messaging_provider`:

```python
def _clear_remote_webhook(channel):
    if not channel.messaging_provider:
        return  # Telegram bails here
    ...
```

`soft_delete()` only flips `deleted=True`; it never calls the form's `post_save`, so the
`if experiment_channel.deleted: webhook_url = None` branch in `_set_telegram_webhook` is
dead code on the normal delete path. **Result: deleting a Telegram channel leaves its
webhook pointed at us at Telegram's servers — it is never cleared.** Standardising fixes
this.

## Goal

Decouple webhook management from `MessagingProvider` so Telegram and provider-backed
platforms share one interface, one dispatch point, and one configuration/teardown path.
Fix the delete leak as a consequence.

Out of scope: the send/receive message paths (`TelegramChannel`, Celery tasks, the
receive view), and any change to how credentials are stored. No DB migration — no model
fields change.

## Design

### 1. `WebhookManager` protocol

A structural `typing.Protocol` capturing the capability that already exists informally on
`MessagingService`:

```python
class WebhookManager(Protocol):
    supports_webhook_management: bool
    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...
    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...
```

`MessagingService` (and therefore `TwilioService`) already satisfies this structurally, so
those classes are **not modified**. The protocol lives in the new module
`apps/channels/webhooks.py`.

### 2. `TelegramWebhookManager`

A small standalone class in `apps/channels/webhooks.py` that wraps `TeleBot`, reading the
per-channel `bot_token` from `extra_data`. All `telebot` imports are confined to this
module.

```python
class TelegramWebhookManager:
    supports_webhook_management = True

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(webhook_url, secret_token=settings.TELEGRAM_SECRET_TOKEN)
        bot.set_my_commands(commands=[types.BotCommand(ExperimentChannel.RESET_COMMAND, "Restart chat")])

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(None)  # clears the webhook at Telegram
```

`set_my_commands` (registering the `/reset` command) rides along with webhook setup, since
provisioning the bot to receive messages is one logical operation. It is a Telegram-only
concept and stays internal to this class. `webhook_url` is part of the
`remove_incoming_webhook` signature for protocol parity with `MessagingService`; the
Telegram implementation does not need it (Telegram has a single webhook per bot).

### 3. Dispatch: `ExperimentChannel.get_webhook_manager()`

A method on the model, placed next to the existing `webhook_url` property. This is the
single point both the form and the delete view consult.

```python
def get_webhook_manager(self) -> "WebhookManager | None":
    if self.messaging_provider:
        return self.messaging_provider.get_messaging_service()
    if self.platform == ChannelPlatform.TELEGRAM:
        from apps.channels.webhooks import TelegramWebhookManager
        return TelegramWebhookManager()
    return None
```

The lazy import keeps `telebot` out of model import time. The `WebhookManager` return type
is imported under `TYPE_CHECKING`.

### 4. `webhook_url` learns Telegram

Add a Telegram branch to the `webhook_url` property so it is meaningful for Telegram and
URL construction lives in one place. Placed before the existing
`if not self.messaging_provider: return ""` guard:

```python
if self.platform == ChannelPlatform.TELEGRAM:
    return absolute_url(reverse("channels:new_telegram_message", args=[self.external_id]), is_secure=True)
```

The duplicate URL construction in `TelegramChannelForm` is removed.

### 5. Unified configuration path

A shared helper on `ExtraFormBase` drives setup for any channel, replacing the bespoke
logic in both `WhatsappChannelForm.post_save` and `TelegramChannelForm.post_save`:

```python
def configure_webhook(self, channel):
    manager = channel.get_webhook_manager()
    if not manager or not manager.supports_webhook_management:
        if channel.webhook_url:
            self.success_message = f"Use the following URL when setting up the webhook: {channel.webhook_url}"
        return
    try:
        manager.set_incoming_webhook(channel.extra_data, channel.webhook_url)
    except Exception:
        logger.exception("Error configuring webhook for channel %s", channel.id)
        self.warning_message = (
            "Could not configure the webhook automatically. "
            f"Use the following URL when setting up the webhook: {channel.webhook_url}"
        )
    else:
        self.success_message = "Webhook configured automatically."
```

- `WhatsappChannelForm.post_save` keeps its WhatsApp-specific behaviour (number resolution
  stays in `clean`) and calls `self.configure_webhook(channel)`.
- `TelegramChannelForm.post_save` becomes a call to the same helper. The webhook-setting
  body moves to `TelegramWebhookManager`; `clean_bot_token` (validation) stays on the form.

#### Deliberate behaviour changes

1. The hardcoded success message **"Webhook configured automatically at Twilio."** becomes
   the generic **"Webhook configured automatically."** (the message is now shared).
2. Telegram's post-save failure mode changes from a **hard `ExperimentChannelException`**
   to the **graceful warning**. Invalid tokens are already caught earlier in
   `clean_bot_token`, so post-save failures are rare; this is an intentional improvement,
   consistent with how WhatsApp already behaves.

### 6. Teardown on delete (bug fix)

`apps/channels/views.py::_clear_remote_webhook` switches from the messaging-provider path
to the unified dispatch:

```python
def _clear_remote_webhook(channel):
    manager = channel.get_webhook_manager()
    if not manager or not manager.supports_webhook_management:
        return
    try:
        manager.remove_incoming_webhook(channel.extra_data or {}, channel.webhook_url)
    except Exception:
        log.exception("Error removing webhook for channel %s", channel.id)
```

Deleting a Telegram channel now clears its webhook at Telegram.

## Affected files

- `apps/channels/webhooks.py` — **new**: `WebhookManager` protocol + `TelegramWebhookManager`.
- `apps/channels/models.py` — add `get_webhook_manager()`; extend `webhook_url` for Telegram.
- `apps/channels/forms.py` — add `ExtraFormBase.configure_webhook()`; rewrite
  `WhatsappChannelForm.post_save` and `TelegramChannelForm.post_save`; move the
  webhook-setting body out of `_set_telegram_webhook` (and remove the now-redundant URL
  construction / `deleted` branch).
- `apps/channels/views.py` — `_clear_remote_webhook` uses `get_webhook_manager()`.

`MessagingService` / `TwilioService` are unchanged (already satisfy the protocol).

## Testing

- `TelegramWebhookManager` (mock `TeleBot`):
  - `set_incoming_webhook` calls `set_webhook(url, secret_token=...)` and `set_my_commands`
    with the token from `extra_data`.
  - `remove_incoming_webhook` calls `set_webhook(None)`.
- `ExperimentChannel.get_webhook_manager()`: returns the service for provider-backed
  channels, a `TelegramWebhookManager` for Telegram, `None` otherwise.
- `ExperimentChannel.webhook_url` returns the receive URL for Telegram.
- `ExtraFormBase.configure_webhook`: success, graceful-failure, and no-manager/manual paths.
- **Regression**: deleting a Telegram channel calls `remove_incoming_webhook` (fails on
  `main` today — the leak).

## Rejected alternatives

- **Give Telegram a real `MessagingProvider`/`TelegramService`.** Maximal literal reuse,
  but creates a semantically empty team-level provider, requires a data migration, and
  conflates the per-channel credential model with the shared-credential model.
- **Only fix the delete leak.** Smallest change, but leaves the two webhook code paths
  divergent and does not address the underlying coupling.

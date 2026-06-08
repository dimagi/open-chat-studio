# Standardise Webhook Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple webhook configuration from `MessagingProvider` so Telegram and provider-backed platforms (Twilio/WhatsApp) share one `WebhookManager` interface, one dispatch point, and one configure/teardown path — fixing the bug where deleting a Telegram channel leaves its webhook live at Telegram.

**Architecture:** Introduce a `WebhookManager` `Protocol` that `MessagingService` already satisfies structurally, plus a tiny `TelegramWebhookManager` that wraps `TeleBot`. `ExperimentChannel.get_webhook_manager()` dispatches to the provider's service or the Telegram manager. A shared `ExtraFormBase.configure_webhook()` helper and the delete view both consult it.

**Tech Stack:** Django, pytest, `pytelegrambotapi` (`telebot`), `unittest.mock`.

Design spec: `docs/superpowers/specs/2026-06-08-standardise-webhook-management-design.md`

---

## File Structure

- **Create** `apps/channels/webhooks.py` — `WebhookManager` protocol + `TelegramWebhookManager`. Sole home for the Telegram `telebot` webhook-provisioning code.
- **Create** `apps/channels/tests/test_webhooks.py` — unit tests for `TelegramWebhookManager`.
- **Modify** `apps/channels/models.py` — add `ExperimentChannel.get_webhook_manager()`; extend `webhook_url` for Telegram; add `TYPE_CHECKING` import.
- **Modify** `apps/channels/forms.py` — add `ExtraFormBase.configure_webhook()`; rewrite `WhatsappChannelForm.post_save` and `TelegramChannelForm.post_save`; delete `TelegramChannelForm._set_telegram_webhook`; drop now-unused imports.
- **Modify** `apps/channels/views.py` — `_clear_remote_webhook` uses `get_webhook_manager()`.
- **Modify** `apps/channels/tests/test_models.py` — tests for `get_webhook_manager` and Telegram `webhook_url`.
- **Modify** `apps/channels/tests/test_forms.py` — update WhatsApp success-message assertion; add Telegram `configure_webhook` test.
- **Modify** `apps/channels/tests/test_delete_channel.py` — regression test for Telegram webhook teardown.
- **Modify** `apps/events/tests/test_scheduled_messages.py` — repoint the `_set_telegram_webhook` patch.

`MessagingService` / `TwilioService` are **not** modified — they already satisfy the protocol.

---

## Task 1: `WebhookManager` protocol + `TelegramWebhookManager`

**Files:**
- Create: `apps/channels/webhooks.py`
- Test: `apps/channels/tests/test_webhooks.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/channels/tests/test_webhooks.py`:

```python
from unittest.mock import patch

from django.conf import settings

from apps.channels.webhooks import TelegramWebhookManager


@patch("apps.channels.webhooks.TeleBot")
def test_set_incoming_webhook_sets_webhook_and_commands(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    manager.set_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with(
        "https://example.com/hook", secret_token=settings.TELEGRAM_SECRET_TOKEN
    )
    bot.set_my_commands.assert_called_once()


@patch("apps.channels.webhooks.TeleBot")
def test_remove_incoming_webhook_clears_webhook(mock_telebot):
    bot = mock_telebot.return_value
    manager = TelegramWebhookManager()

    manager.remove_incoming_webhook({"bot_token": "tok"}, "https://example.com/hook")

    mock_telebot.assert_called_once_with("tok", threaded=False)
    bot.set_webhook.assert_called_once_with(None)


def test_supports_webhook_management():
    assert TelegramWebhookManager.supports_webhook_management is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/channels/tests/test_webhooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.channels.webhooks'`

- [ ] **Step 3: Create the implementation**

Create `apps/channels/webhooks.py`:

```python
from typing import Protocol

from django.conf import settings
from telebot import TeleBot, types

from apps.channels.models import ExperimentChannel


class WebhookManager(Protocol):
    """Configures a channel's inbound message webhook at the upstream provider.

    Satisfied structurally by both MessagingService (provider-backed platforms) and
    TelegramWebhookManager (per-channel bot token).
    """

    supports_webhook_management: bool

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None: ...


class TelegramWebhookManager:
    """Manages the Telegram bot webhook using the per-channel bot token in extra_data."""

    supports_webhook_management = True

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(webhook_url, secret_token=settings.TELEGRAM_SECRET_TOKEN)
        bot.set_my_commands(commands=[types.BotCommand(ExperimentChannel.RESET_COMMAND, "Restart chat")])

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str) -> None:
        bot = TeleBot(extra_data.get("bot_token", ""), threaded=False)
        bot.set_webhook(None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/channels/tests/test_webhooks.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check apps/channels/webhooks.py apps/channels/tests/test_webhooks.py --fix
uv run ruff format apps/channels/webhooks.py apps/channels/tests/test_webhooks.py
git add apps/channels/webhooks.py apps/channels/tests/test_webhooks.py
git commit -m "Add WebhookManager protocol and TelegramWebhookManager"
```

---

## Task 2: `ExperimentChannel.get_webhook_manager()`

**Files:**
- Modify: `apps/channels/models.py` (top imports; new method after `webhook_url`, near line 270)
- Test: `apps/channels/tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/channels/tests/test_models.py`:

```python
@pytest.mark.django_db()
def test_get_webhook_manager_returns_telegram_manager_for_telegram():
    from apps.channels.webhooks import TelegramWebhookManager

    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})

    assert isinstance(channel.get_webhook_manager(), TelegramWebhookManager)


@pytest.mark.django_db()
def test_get_webhook_manager_returns_service_for_provider_backed_channel():
    provider = MessagingProviderFactory(
        type=MessagingProviderType.twilio, config={"account_sid": "123", "auth_token": "123"}
    )
    channel = ExperimentChannelFactory(
        platform=ChannelPlatform.WHATSAPP, messaging_provider=provider, extra_data={"number": "+12125552368"}
    )

    manager = channel.get_webhook_manager()

    assert manager.supports_webhook_management is True


@pytest.mark.django_db()
def test_get_webhook_manager_returns_none_for_web_channel():
    channel = ExperimentChannelFactory(platform=ChannelPlatform.WEB, messaging_provider=None, extra_data={})

    assert channel.get_webhook_manager() is None
```

Ensure these imports exist at the top of `test_models.py` (add any that are missing):

```python
import pytest

from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/channels/tests/test_models.py -k get_webhook_manager -v`
Expected: FAIL — `AttributeError: 'ExperimentChannel' object has no attribute 'get_webhook_manager'`

- [ ] **Step 3: Add the TYPE_CHECKING import**

In `apps/channels/models.py`, change line 2:

```python
from typing import Self, cast
```

to:

```python
from typing import TYPE_CHECKING, Self, cast
```

Then add, immediately after the existing top-level imports block (after line 15, `from apps.web.meta import absolute_url`):

```python
if TYPE_CHECKING:
    from apps.channels.webhooks import WebhookManager
```

- [ ] **Step 4: Add the method**

In `apps/channels/models.py`, add this method directly after the `webhook_url` property (after line 270, before `soft_delete`):

```python
    def get_webhook_manager(self) -> "WebhookManager | None":
        """Return the object that manages this channel's inbound webhook, or None.

        Provider-backed channels delegate to their MessagingService; Telegram uses its
        per-channel bot token. Both satisfy the WebhookManager protocol structurally.
        """
        if self.messaging_provider:
            return self.messaging_provider.get_messaging_service()
        if self.platform == ChannelPlatform.TELEGRAM:
            from apps.channels.webhooks import (  # noqa: PLC0415 - lazy: avoid importing telebot at module load
                TelegramWebhookManager,
            )

            return TelegramWebhookManager()
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/channels/tests/test_models.py -k get_webhook_manager -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check apps/channels/models.py apps/channels/tests/test_models.py --fix
uv run ruff format apps/channels/models.py apps/channels/tests/test_models.py
uv run ty check apps/channels/models.py
git add apps/channels/models.py apps/channels/tests/test_models.py
git commit -m "Add ExperimentChannel.get_webhook_manager()"
```

---

## Task 3: `webhook_url` learns Telegram

**Files:**
- Modify: `apps/channels/models.py` (`webhook_url` property, lines 245-270)
- Test: `apps/channels/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/channels/tests/test_models.py`:

```python
@pytest.mark.django_db()
def test_webhook_url_for_telegram_channel():
    channel = ExperimentChannelFactory(platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"})

    url = channel.webhook_url

    assert str(channel.external_id) in url
    assert url.startswith("https://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/channels/tests/test_models.py -k test_webhook_url_for_telegram_channel -v`
Expected: FAIL — `assert ... in ''` (returns empty string today)

- [ ] **Step 3: Add the Telegram branch**

In `apps/channels/models.py`, inside the `webhook_url` property, add the Telegram branch **before** the `if not self.messaging_provider: return ""` guard. The start of the property becomes:

```python
    @property
    def webhook_url(self) -> str:
        """The wehook URL that should be used in external services"""
        from apps.service_providers.models import (  # noqa: PLC0415 - circular: service_providers.models imports channels.models
            MessagingProviderType,
        )

        if self.platform == ChannelPlatform.TELEGRAM:
            return absolute_url(
                reverse("channels:new_telegram_message", args=[self.external_id]), is_secure=True
            )

        if not self.messaging_provider:
            return ""
```

(Leave the rest of the property unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/channels/tests/test_models.py -k test_webhook_url_for_telegram_channel -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check apps/channels/models.py apps/channels/tests/test_models.py --fix
uv run ruff format apps/channels/models.py apps/channels/tests/test_models.py
git add apps/channels/models.py apps/channels/tests/test_models.py
git commit -m "Build Telegram receive URL in webhook_url"
```

---

## Task 4: Unified `configure_webhook` helper + rewire forms

**Files:**
- Modify: `apps/channels/forms.py` (`ExtraFormBase`; `WhatsappChannelForm.post_save`; `TelegramChannelForm`)
- Test: `apps/channels/tests/test_forms.py`

- [ ] **Step 1: Write/adjust the failing tests**

In `apps/channels/tests/test_forms.py`, update the existing WhatsApp success-message assertion. Change (around line 299):

```python
    assert form.success_message == "Webhook configured automatically at Twilio."
```

to:

```python
    assert form.success_message == "Webhook configured automatically."
```

Then append a Telegram test:

```python
@pytest.mark.django_db()
@patch("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook")
def test_telegram_post_save_configures_webhook(set_incoming_webhook, experiment):
    channel = ExperimentChannelFactory(
        experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"}
    )
    form = TelegramChannelForm(experiment=experiment, data={"bot_token": "tok"})

    form.post_save(channel)

    set_incoming_webhook.assert_called_once_with(channel.extra_data, channel.webhook_url)
    assert form.success_message == "Webhook configured automatically."
    assert form.warning_message == ""


@pytest.mark.django_db()
@patch("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook")
def test_telegram_post_save_falls_back_to_warning_on_failure(set_incoming_webhook, experiment):
    set_incoming_webhook.side_effect = Exception("Telegram is down")
    channel = ExperimentChannelFactory(
        experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"}
    )
    form = TelegramChannelForm(experiment=experiment, data={"bot_token": "tok"})

    form.post_save(channel)

    assert channel.webhook_url in form.warning_message
    assert form.success_message == ""
```

Confirm `TelegramChannelForm` is imported at the top of `test_forms.py` (add to the existing `from apps.channels.forms import (...)` if missing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/channels/tests/test_forms.py -k "telegram_post_save or configures_twilio_webhook" -v`
Expected: FAIL — the new Telegram tests fail (the Telegram form does not yet build a `TelegramWebhookManager`; `set_incoming_webhook` not called), and/or the patch target resolves but the form still calls the old code path.

- [ ] **Step 3: Add `configure_webhook` to `ExtraFormBase`**

In `apps/channels/forms.py`, add this method to `ExtraFormBase` (after `post_save`, around line 164):

```python
    def configure_webhook(self, channel: ExperimentChannel):
        """Point the channel's inbound webhook at us, via its WebhookManager.

        Falls back to manual setup instructions when the channel has no manager or the
        manager cannot configure webhooks. On failure, surfaces a warning rather than
        raising, so channel creation still succeeds.
        """
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

- [ ] **Step 4: Rewrite `WhatsappChannelForm.post_save`**

In `apps/channels/forms.py`, replace the entire `WhatsappChannelForm.post_save` method (currently lines 246-261) with:

```python
    def post_save(self, channel: ExperimentChannel):
        self.configure_webhook(channel)
```

(Leave `WhatsappChannelForm.clean_number` and `clean` unchanged.)

- [ ] **Step 5: Rewrite `TelegramChannelForm` — use the helper, delete `_set_telegram_webhook`**

In `apps/channels/forms.py`, replace `TelegramChannelForm.post_save` and **delete** `_set_telegram_webhook` entirely. The class body becomes (keeping `bot_token` field and `clean_bot_token` exactly as they are):

```python
class TelegramChannelForm(ExtraFormBase):
    bot_token = forms.CharField(label="Bot Token", max_length=100)

    def post_save(self, channel: ExperimentChannel):
        self.configure_webhook(channel)

    def clean_bot_token(self):
        """Checks the bot token by making a request to get info on the bot. If the token is invalid, an
        ApiTelegramException will be raised with error_code = 404
        """
        bot_token = self.cleaned_data["bot_token"]
        try:
            bot = TeleBot(bot_token, threaded=False)
            bot.get_me()
        except apihelper.ApiTelegramException as ex:
            if ex.error_code == 404:
                raise forms.ValidationError(f"Invalid token: {bot_token}") from None
            else:
                logger.exception(ex)
                raise forms.ValidationError("Could not verify the bot token") from None
        return bot_token
```

- [ ] **Step 6: Drop now-unused imports in `forms.py`**

`reverse`, `absolute_url`, and `types` were only used by the deleted `_set_telegram_webhook`. Remove them:
- Line 12: delete `from django.urls import reverse`
- Line 28: delete `from apps.web.meta import absolute_url`
- Line 13: change `from telebot import TeleBot, apihelper, types` to `from telebot import TeleBot, apihelper`

`ruff check --fix` in the next step will confirm none are still referenced (it errors on a still-used import being removed). Keep `settings`, `TeleBot`, and `apihelper` — still used elsewhere.

- [ ] **Step 7: Run the form tests**

Run: `uv run pytest apps/channels/tests/test_forms.py -v`
Expected: PASS (all, including the updated WhatsApp assertion and the two new Telegram tests)

- [ ] **Step 8: Lint, typecheck, commit**

```bash
uv run ruff check apps/channels/forms.py apps/channels/tests/test_forms.py --fix
uv run ruff format apps/channels/forms.py apps/channels/tests/test_forms.py
uv run ty check apps/channels/forms.py
git add apps/channels/forms.py apps/channels/tests/test_forms.py
git commit -m "Route channel webhook setup through configure_webhook"
```

---

## Task 5: Teardown on delete + repoint stale patch

**Files:**
- Modify: `apps/channels/views.py` (`_clear_remote_webhook`, lines 433-443)
- Modify: `apps/events/tests/test_scheduled_messages.py` (line 209 patch target)
- Test: `apps/channels/tests/test_delete_channel.py`

- [ ] **Step 1: Write the failing regression test**

Append to `apps/channels/tests/test_delete_channel.py`:

```python
@pytest.mark.django_db()
@patch("apps.channels.webhooks.TelegramWebhookManager.remove_incoming_webhook")
def test_delete_telegram_channel_clears_remote_webhook(remove_incoming_webhook, client, team_with_users):
    experiment = ExperimentFactory(team=team_with_users)
    channel = ExperimentChannelFactory(
        team=team_with_users,
        experiment=experiment,
        platform=ChannelPlatform.TELEGRAM,
        extra_data={"bot_token": "tok"},
    )

    response = _delete_channel(client, team_with_users, channel)

    assert response.status_code == 200
    channel.refresh_from_db()
    assert channel.deleted
    remove_incoming_webhook.assert_called_once_with(channel.extra_data, channel.webhook_url)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/channels/tests/test_delete_channel.py -k telegram -v`
Expected: FAIL — `remove_incoming_webhook` not called (today `_clear_remote_webhook` returns early because Telegram has no `messaging_provider`)

- [ ] **Step 3: Rewrite `_clear_remote_webhook`**

In `apps/channels/views.py`, replace `_clear_remote_webhook` (lines 433-443) with:

```python
def _clear_remote_webhook(channel: ExperimentChannel):
    """Best-effort removal of the channel's webhook configuration at the upstream provider."""
    manager = channel.get_webhook_manager()
    if not manager or not manager.supports_webhook_management:
        return
    try:
        manager.remove_incoming_webhook(channel.extra_data or {}, channel.webhook_url)
    except Exception:
        log.exception("Error removing webhook for channel %s", channel.id)
```

Verify `ExperimentChannel` is already imported in `views.py` (it is used throughout); if the type hint triggers an import error, drop the annotation rather than adding an import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest apps/channels/tests/test_delete_channel.py -v`
Expected: PASS (all, including the existing WhatsApp delete tests)

- [ ] **Step 5: Repoint the stale `_set_telegram_webhook` patch**

In `apps/events/tests/test_scheduled_messages.py`, line 209, change:

```python
@patch("apps.channels.forms.TelegramChannelForm._set_telegram_webhook")
def test_error_when_sending_sending_message_to_a_user(_set_telegram_webhook, caplog):
```

to:

```python
@patch("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook")
def test_error_when_sending_sending_message_to_a_user(set_incoming_webhook, caplog):
```

(The patched symbol just prevents a real Telegram call; renaming the arg keeps it consistent.)

- [ ] **Step 6: Run the affected test**

Run: `uv run pytest apps/events/tests/test_scheduled_messages.py::test_error_when_sending_sending_message_to_a_user -v`
Expected: PASS

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check apps/channels/views.py apps/channels/tests/test_delete_channel.py apps/events/tests/test_scheduled_messages.py --fix
uv run ruff format apps/channels/views.py apps/channels/tests/test_delete_channel.py apps/events/tests/test_scheduled_messages.py
uv run ty check apps/channels/views.py
git add apps/channels/views.py apps/channels/tests/test_delete_channel.py apps/events/tests/test_scheduled_messages.py
git commit -m "Clear Telegram webhook on channel delete"
```

---

## Task 6: Full-suite verification

- [ ] **Step 1: Run the touched app test suites**

Run:
```bash
uv run pytest apps/channels apps/events/tests/test_scheduled_messages.py -q
```
Expected: PASS (no regressions). If anything fails, fix before proceeding — do not mark complete with failing tests.

- [ ] **Step 2: Final lint + typecheck of all changed files**

Run:
```bash
uv run ruff check apps/channels/webhooks.py apps/channels/models.py apps/channels/forms.py apps/channels/views.py
uv run ty check apps/channels/
```
Expected: clean.

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** protocol (T1), Telegram manager incl. `set_my_commands` (T1), dispatch method (T2), `webhook_url` Telegram branch (T3), unified `configure_webhook` + generic success message + WhatsApp/Telegram rewrite (T4), delete teardown bug fix (T5). All spec sections map to a task.
- **Behaviour changes (intended, per spec):** success message "...at Twilio." → "...automatically." (T4 step 1 updates the assertion); Telegram post-save failure now warns instead of raising `ExperimentChannelException` (covered by T4 failure test).
- **No migration:** no model fields change.
- **Method/name consistency:** `get_webhook_manager`, `configure_webhook`, `set_incoming_webhook`, `remove_incoming_webhook`, `supports_webhook_management` used identically across tasks and match the existing `MessagingService` API.

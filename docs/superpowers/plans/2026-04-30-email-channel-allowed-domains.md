# Email Channel Allowed Domains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the email channel to a configured set of domains via a single
Django setting (`EMAIL_CHANNEL_ALLOWED_DOMAINS`), enforcing the rule both in the
inbound webhook handler (drop mail to disallowed `to_address`) and in the
`EmailChannelForm` (reject saves with disallowed `email_address` /
`from_address`, surface allowed list in help text).

**Architecture:** Single env-var-backed list in `config/settings.py`, parsed via
`env.list(...)`. Two helper functions in `apps/channels/utils.py`
(`is_email_domain_allowed`, `get_allowed_email_domains`) reuse the existing
`match_domain_pattern` (which already handles `*.example.com` wildcards). Form
adds `clean_email_address` / `clean_from_address` plus help-text injection in
`__init__`. Inbound handler adds a pre-filter call after the parse step.
Fail-closed: empty/unset setting rejects everything.

**Tech Stack:** Django 5, django-environ for env parsing, pytest + pytest-django
for tests, `@override_settings` for per-test domain configs.

---

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `config/settings.py` | Modify | Define `EMAIL_CHANNEL_ALLOWED_DOMAINS` from env. |
| `.env.example` | Modify | Document the new env var. |
| `apps/channels/utils.py` | Modify | Add `is_email_domain_allowed` + `get_allowed_email_domains`. |
| `apps/channels/forms.py` | Modify | `EmailChannelForm`: validators + help text. |
| `apps/channels/channels_v2/email_channel.py` | Modify | Pre-filter in `email_inbound_handler`. |
| `apps/channels/tests/test_email_channel.py` | Modify | New tests + `@override_settings` on existing tests so they keep passing under fail-closed default. |

No new files. No DB migration. No template changes.

---

## Task 1: Add the Django setting + document it

**Files:**
- Modify: `config/settings.py:401-410` (the existing email block)
- Modify: `.env.example:45-58` (the existing email config block)

- [ ] **Step 1: Add the setting in config/settings.py**

Open `config/settings.py`. After line 410 (the `ANYMAIL_WEBHOOK_SECRET` block),
add:

```python

# Inbound email channel restrictions.
# Comma-separated list of domains the email channel will accept.
# Supports wildcard subdomains (e.g. "*.example.com").
# Empty / unset => fail-closed: no inbound email is processed and no email
# channel can be saved via the form.
EMAIL_CHANNEL_ALLOWED_DOMAINS = env.list("EMAIL_CHANNEL_ALLOWED_DOMAINS", default=[])
```

- [ ] **Step 2: Document the env var in .env.example**

In `.env.example`, after line 58 (end of `## Production Email Config` block),
append:

```
## Inbound email channel
## Comma-separated list of domains the email channel will accept (inbound to_address
## and channel-form email_address/from_address). Supports wildcards like *.example.com.
## Empty/unset => fail-closed (no inbound mail processed, no form saves).
# EMAIL_CHANNEL_ALLOWED_DOMAINS=chat.openchatstudio.com,*.example.com
```

- [ ] **Step 3: Verify the setting loads cleanly**

Run:

```bash
uv run python -c "from django.conf import settings; import django; django.setup(); print(repr(settings.EMAIL_CHANNEL_ALLOWED_DOMAINS))"
```

Expected output: `[]` (empty list, since the env var is not set in the dev env).

- [ ] **Step 4: Lint and format**

```bash
uv run ruff check config/settings.py --fix
uv run ruff format config/settings.py
```

- [ ] **Step 5: Commit**

```bash
git add config/settings.py .env.example
git commit -m "feat: add EMAIL_CHANNEL_ALLOWED_DOMAINS setting"
```

---

## Task 2: Add domain helpers in apps/channels/utils.py

**Files:**
- Modify: `apps/channels/utils.py` (add 2 functions, 1 import)
- Modify: `apps/channels/tests/test_utils.py` if it exists, otherwise add to `apps/channels/tests/test_email_channel.py`

First, check whether a utils test module exists:

```bash
ls apps/channels/tests/ | grep -i util
```

If a `test_utils.py` exists, add the new test class there. Otherwise, add a new
test class `TestEmailDomainAllowlist` to `apps/channels/tests/test_email_channel.py`
right after `TestEmailMessageParse` (around line 142). The instructions below
assume the latter location; adapt the file path if a `test_utils.py` exists.

- [ ] **Step 1: Write failing tests for `is_email_domain_allowed` and `get_allowed_email_domains`**

Open `apps/channels/tests/test_email_channel.py`. Add this import near the
existing imports at the top (don't duplicate existing imports):

```python
from django.test import override_settings
```

Then add the new test class. Place it after `class TestEmailMessageParse`
(currently ends near line 142):

```python
class TestEmailDomainAllowlist:
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_exact_match_allowed(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("user@example.com") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_wildcard_match_allowed(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("user@mail.foo.com") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_bare_domain_does_not_match_wildcard(self):
        from apps.channels.utils import is_email_domain_allowed

        # *.foo.com matches subdomains, not the bare domain.
        assert is_email_domain_allowed("user@foo.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_disallowed_domain_rejected(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("user@bar.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_empty_setting_rejects_everything(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("user@example.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_malformed_address_rejected(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("not-an-email") is False
        assert is_email_domain_allowed("") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_case_insensitive_match(self):
        from apps.channels.utils import is_email_domain_allowed

        assert is_email_domain_allowed("user@Example.COM") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_get_allowed_email_domains_returns_list(self):
        from apps.channels.utils import get_allowed_email_domains

        assert get_allowed_email_domains() == ["example.com", "*.foo.com"]

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_get_allowed_email_domains_returns_empty_list_when_unset(self):
        from apps.channels.utils import get_allowed_email_domains

        assert get_allowed_email_domains() == []
```

(The `from apps.channels.utils import ...` is intentionally inside each test
body for the moment so the *first* test run fails on import — this is the
failing-test step. We move imports to the top in step 4.)

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailDomainAllowlist -v
```

Expected: each test fails with `ImportError` or `AttributeError` because
`is_email_domain_allowed` / `get_allowed_email_domains` don't exist yet.

- [ ] **Step 3: Implement the helpers in apps/channels/utils.py**

Open `apps/channels/utils.py`. The file currently starts with:

```python
from __future__ import annotations

from urllib.parse import urlparse

from django.core.cache import cache
from django.core.validators import validate_domain_name  # ty: ignore[unresolved-import]

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, ExperimentSession
```

Add `from django.conf import settings` immediately after the `from urllib.parse import urlparse` line, so the imports become:

```python
from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.validators import validate_domain_name  # ty: ignore[unresolved-import]

from apps.channels.exceptions import ExperimentChannelException
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, ExperimentSession
```

Then, after the existing `validate_domain` function (currently lines 40-44),
add:

```python


def is_email_domain_allowed(email_address: str) -> bool:
    """Return True if email_address is on a domain in EMAIL_CHANNEL_ALLOWED_DOMAINS.

    Returns False for malformed addresses (no '@') and when the setting is
    empty/unset (fail-closed).
    """
    if not email_address or "@" not in email_address:
        return False
    domain = email_address.rsplit("@", 1)[1].lower()
    allowed = settings.EMAIL_CHANNEL_ALLOWED_DOMAINS
    if not allowed:
        return False
    return any(match_domain_pattern(domain, pattern.lower()) for pattern in allowed)


def get_allowed_email_domains() -> list[str]:
    """Return the configured allowed-domains list, for UI display."""
    return list(settings.EMAIL_CHANNEL_ALLOWED_DOMAINS)
```

- [ ] **Step 4: Move the per-test imports to the top of the test file**

In `apps/channels/tests/test_email_channel.py`, remove the `from apps.channels.utils import ...`
lines from inside each test body and add a single import at the top of the file
(near the existing `from apps.channels.utils ...` if any, otherwise grouped
with the other `apps.channels` imports):

```python
from apps.channels.utils import get_allowed_email_domains, is_email_domain_allowed
```

The test bodies now use the imported names directly (e.g., `assert is_email_domain_allowed("user@example.com") is True`).

- [ ] **Step 5: Run the tests to confirm they pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailDomainAllowlist -v
```

Expected: all 9 tests pass.

- [ ] **Step 6: Lint, format, type-check**

```bash
uv run ruff check apps/channels/utils.py apps/channels/tests/test_email_channel.py --fix
uv run ruff format apps/channels/utils.py apps/channels/tests/test_email_channel.py
uv run ty check apps/channels/utils.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/channels/utils.py apps/channels/tests/test_email_channel.py
git commit -m "feat: add is_email_domain_allowed helper for email channel allowlist"
```

---

## Task 3: Wire the inbound pre-filter into email_inbound_handler

**Files:**
- Modify: `apps/channels/channels_v2/email_channel.py:254-304` (the `email_inbound_handler` function)
- Modify: `apps/channels/tests/test_email_channel.py` (add new tests, update existing tests with `@override_settings`)

This task changes inbound behavior. The existing `TestEmailInboundHandler` tests
(currently lines 474-545) call `email_inbound_handler` with `to_email`s on
`chat.openchatstudio.com` and `nowhere.com` and expect specific
called/not-called outcomes. With the fail-closed default they will all break.
We update them with `@override_settings` first (so they keep testing what they
were meant to test), then add new coverage for the allowlist itself.

- [ ] **Step 1: Write the new failing tests**

Add the following to `class TestEmailInboundHandler` in
`apps/channels/tests/test_email_channel.py` (append at the end of the existing
class, after `test_parse_failure_does_not_raise`):

```python
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_to_address_on_disallowed_domain_dropped(self, team_with_users):
        team = team_with_users
        _make_email_channel(team, email_address="bot@chat.openchatstudio.com", is_default=True)
        inbound = _make_inbound_message(to_email="someone@evil.example.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["*.example.com"])
    def test_to_address_wildcard_match_allowed(self, team_with_users):
        team = team_with_users
        _make_email_channel(team, email_address="bot@mail.example.com")
        inbound = _make_inbound_message(to_email="bot@mail.example.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_called_once()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_empty_setting_drops_inbound(self, team_with_users):
        team = team_with_users
        _make_email_channel(team, email_address="bot@chat.openchatstudio.com", is_default=True)
        inbound = _make_inbound_message(to_email="bot@chat.openchatstudio.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_thread_reply_to_disallowed_domain_still_dropped(self, team_with_users):
        """Even an existing-thread reply is dropped if to_address is not allowed."""
        team = team_with_users
        channel = _make_email_channel(team, email_address="bot@chat.openchatstudio.com")
        _make_session(team, channel, "<outbound-1@chat.openchatstudio.com>")

        inbound = _make_inbound_message(
            to_email="reply@evil.example.com",
            in_reply_to="<outbound-1@chat.openchatstudio.com>",
        )

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_malformed_to_address_dropped(self, team_with_users):
        team = team_with_users
        _make_email_channel(team, email_address="bot@chat.openchatstudio.com", is_default=True)
        inbound = _make_inbound_message(to_email="not-an-email")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailInboundHandler -v
```

Expected: the 5 new tests fail (mock_task.delay is called when we expected it
not to be, because the pre-filter doesn't exist yet). The pre-existing
`TestEmailInboundHandler` tests should still pass at this point — they don't
depend on the new setting.

- [ ] **Step 3: Add the pre-filter in email_inbound_handler**

Open `apps/channels/channels_v2/email_channel.py`. In the imports at the top of
the file (around line 16-22), add:

```python
from apps.channels.utils import is_email_domain_allowed
```

Then in `email_inbound_handler` (currently line 254), insert the domain check
**after** the parse block and **before** the existing `has_existing_session`
pre-filter. The existing code block:

```python
    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    # Best-effort pre-filter: enqueue if any email channel could handle this.
```

Becomes:

```python
    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    if not is_email_domain_allowed(email_msg.to_address):
        logger.info(
            "Rejecting inbound email: to-domain not allowed (to=%s)",
            email_msg.to_address,
        )
        return

    # Best-effort pre-filter: enqueue if any email channel could handle this.
```

- [ ] **Step 4: Run the new tests to confirm they pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailInboundHandler -v -k "to_address_on_disallowed or wildcard_match or empty_setting_drops or thread_reply_to_disallowed or malformed_to_address"
```

Expected: all 5 new tests pass.

- [ ] **Step 5: Update existing TestEmailInboundHandler tests with @override_settings**

The 6 existing tests in `TestEmailInboundHandler` (around lines 475-545) will
now mostly fail because `EMAIL_CHANNEL_ALLOWED_DOMAINS` is empty by default.

Apply a class-level decorator. Change:

```python
class TestEmailInboundHandler:
    def test_enqueues_task(self, team_with_users):
```

To:

```python
@override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
class TestEmailInboundHandler:
    def test_enqueues_task(self, team_with_users):
```

The `test_no_channel_silently_ignored` test currently uses
`to_email="unknown@nowhere.com"`. Under the new behavior the email is dropped
because `nowhere.com` is not in the allowlist, **not** because no channel
exists. Update that test so it still proves what it claims to prove. Replace:

```python
    def test_no_channel_silently_ignored(self):
        """Unmatched email is silently ignored (no bounce loop)."""
        inbound = _make_inbound_message(to_email="unknown@nowhere.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()
```

With:

```python
    def test_no_channel_silently_ignored(self):
        """Unmatched email is silently ignored (no bounce loop).

        Allowed domain but no channel exists -> the existing has_channel
        pre-filter drops it.
        """
        inbound = _make_inbound_message(to_email="unknown@chat.openchatstudio.com")

        with patch("apps.channels.tasks.handle_email_message") as mock_task:
            mock_task.delay = MagicMock()
            email_inbound_handler(sender=None, event=MagicMock(message=inbound))
            mock_task.delay.assert_not_called()
```

(The class-level `@override_settings` makes `chat.openchatstudio.com` allowed,
so this now exercises the no-channel branch rather than the new domain branch.)

- [ ] **Step 6: Run the entire TestEmailInboundHandler class**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailInboundHandler -v
```

Expected: all tests pass (existing 6 + new 5 = 11).

- [ ] **Step 7: Lint, format, type-check**

```bash
uv run ruff check apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py --fix
uv run ruff format apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
uv run ty check apps/channels/channels_v2/email_channel.py
```

- [ ] **Step 8: Commit**

```bash
git add apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
git commit -m "feat: drop inbound email when to-domain is not allowed"
```

---

## Task 4: Form validation + help-text hint in EmailChannelForm

**Files:**
- Modify: `apps/channels/forms.py:660-690` (`EmailChannelForm`)
- Modify: `apps/channels/tests/test_email_channel.py` (add new tests, update existing form tests with `@override_settings`)

- [ ] **Step 1: Write the failing form tests**

Append the following tests to `class TestEmailChannelForm` in
`apps/channels/tests/test_email_channel.py` (after the existing
`test_duplicate_default_allowed_when_editing_same_channel`, around line 203):

```python
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_email_address_on_allowed_domain_accepted(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["*.openchatstudio.com"])
    def test_email_address_on_allowed_wildcard_accepted(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_email_address_on_disallowed_domain_rejected(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@evil.example.com", "platform": "email"},
        )
        assert not form.is_valid()
        assert "email_address" in form.errors
        # Error message should mention the allowed list so admins can self-serve.
        assert "chat.openchatstudio.com" in str(form.errors["email_address"])

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_email_address_rejected_when_setting_empty(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert not form.is_valid()
        assert "email_address" in form.errors
        assert "no allowed domains" in str(form.errors["email_address"]).lower()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_from_address_validated_when_set(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "from_address": "noreply@evil.example.com",
                "platform": "email",
            },
        )
        assert not form.is_valid()
        assert "from_address" in form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_from_address_skipped_when_blank(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "from_address": "",
                "platform": "email",
            },
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com", "*.example.com"])
    def test_help_text_lists_allowed_domains(self, experiment):
        form = EmailChannelForm(experiment=experiment)
        help_text = form.fields["email_address"].help_text
        assert "chat.openchatstudio.com" in help_text
        assert "*.example.com" in help_text

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_help_text_warns_when_no_domains_configured(self, experiment):
        form = EmailChannelForm(experiment=experiment)
        help_text = form.fields["email_address"].help_text
        assert "no allowed domains" in help_text.lower()
```

- [ ] **Step 2: Run the new form tests to confirm they fail**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailChannelForm -v -k "allowed_domain or allowed_wildcard or disallowed_domain or rejected_when_setting_empty or from_address_validated or from_address_skipped or help_text"
```

Expected: all 8 new tests fail (forms accept disallowed addresses; help text
doesn't mention domains).

- [ ] **Step 3: Update EmailChannelForm with validators and help-text hint**

Open `apps/channels/forms.py`. The current `EmailChannelForm` (line 660) is:

```python
class EmailChannelForm(ExtraFormBase):
    email_address = forms.EmailField(
        label="Email Address",
        help_text=(
            "The email address that will receive messages for this channel (e.g., support@chat.openchatstudio.com)"
        ),
    )
    from_address = forms.EmailField(
        label="From Address",
        required=False,
        help_text="Optional: override the From address on outbound replies. Defaults to the system email.",
    )
    is_default = forms.BooleanField(
        label="Default fallback channel",
        required=False,
        help_text="When enabled, this channel receives emails that don't match any other email channel address.",
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_default"):
            existing = ExperimentChannel.objects.filter(
                platform=ChannelPlatform.EMAIL,
                extra_data__contains={"is_default": True},
                deleted=False,
            )
            if self.channel:
                existing = existing.exclude(pk=self.channel.pk)
            if existing.exists():
                self.add_error("is_default", "Another email channel is already set as the default.")
        return cleaned_data
```

First, ensure the helpers are importable. At the top of `forms.py`, find the
existing `from apps.channels.utils import ALL_DOMAINS, validate_domain_or_wildcard, validate_platform_availability`
import (around line 18) and extend it:

```python
from apps.channels.utils import (
    ALL_DOMAINS,
    get_allowed_email_domains,
    is_email_domain_allowed,
    validate_domain_or_wildcard,
    validate_platform_availability,
)
```

Then replace the `EmailChannelForm` body with:

```python
class EmailChannelForm(ExtraFormBase):
    email_address = forms.EmailField(
        label="Email Address",
        help_text=(
            "The email address that will receive messages for this channel (e.g., support@chat.openchatstudio.com)"
        ),
    )
    from_address = forms.EmailField(
        label="From Address",
        required=False,
        help_text="Optional: override the From address on outbound replies. Defaults to the system email.",
    )
    is_default = forms.BooleanField(
        label="Default fallback channel",
        required=False,
        help_text="When enabled, this channel receives emails that don't match any other email channel address.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = get_allowed_email_domains()
        if allowed:
            hint = f" Allowed domains: {', '.join(allowed)}."
        else:
            hint = " No allowed domains are currently configured. Contact your administrator before saving."
        self.fields["email_address"].help_text += hint

    def _validate_domain(self, address: str, field_name: str) -> str:
        if not is_email_domain_allowed(address):
            allowed = get_allowed_email_domains()
            if allowed:
                msg = (
                    f"Domain is not in the allowed list. Allowed: {', '.join(allowed)}."
                )
            else:
                msg = (
                    "No allowed domains are currently configured. "
                    "Contact your administrator."
                )
            raise ValidationError(msg)
        return address

    def clean_email_address(self):
        return self._validate_domain(self.cleaned_data["email_address"], "email_address")

    def clean_from_address(self):
        value = self.cleaned_data.get("from_address", "")
        if not value:
            return value
        return self._validate_domain(value, "from_address")

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_default"):
            existing = ExperimentChannel.objects.filter(
                platform=ChannelPlatform.EMAIL,
                extra_data__contains={"is_default": True},
                deleted=False,
            )
            if self.channel:
                existing = existing.exclude(pk=self.channel.pk)
            if existing.exists():
                self.add_error("is_default", "Another email channel is already set as the default.")
        return cleaned_data
```

Notes:
- `ValidationError` is already imported at the top of `forms.py` (used by
  `EmbeddedWidgetChannelForm`). If your file lacks the import, add
  `from django.core.exceptions import ValidationError`.
- `_validate_domain` takes `field_name` for future use / log clarity even
  though it isn't strictly needed for the message; keep the signature for
  symmetry with the `clean_<field>` callers.

- [ ] **Step 4: Run the new form tests to confirm they pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailChannelForm -v -k "allowed_domain or allowed_wildcard or disallowed_domain or rejected_when_setting_empty or from_address_validated or from_address_skipped or help_text"
```

Expected: all 8 new tests pass.

- [ ] **Step 5: Update existing TestEmailChannelForm tests with @override_settings**

The pre-existing tests in `TestEmailChannelForm` (currently lines 145-203) all
use `support@chat.openchatstudio.com` / `first@chat.openchatstudio.com` etc.
They'll fail under the empty default. Apply a class-level override.

Change:

```python
@pytest.mark.django_db()
class TestEmailChannelForm:
    def test_valid_form(self, experiment):
```

To:

```python
@pytest.mark.django_db()
@override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
class TestEmailChannelForm:
    def test_valid_form(self, experiment):
```

The four new tests that need their *own* override (because they configure a
different list, e.g. `[]` or `*.example.com`) already have method-level
`@override_settings` — Django merges class-level + method-level, with method
taking precedence, so this works correctly without further changes.

- [ ] **Step 6: Run the entire TestEmailChannelForm class**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailChannelForm -v
```

Expected: all tests pass (existing 6 + new 8 = 14).

- [ ] **Step 7: Lint, format, type-check**

```bash
uv run ruff check apps/channels/forms.py apps/channels/tests/test_email_channel.py --fix
uv run ruff format apps/channels/forms.py apps/channels/tests/test_email_channel.py
uv run ty check apps/channels/forms.py
```

- [ ] **Step 8: Commit**

```bash
git add apps/channels/forms.py apps/channels/tests/test_email_channel.py
git commit -m "feat: validate email channel address against allowed-domains setting"
```

---

## Task 5: Audit other email-channel tests for fail-closed regressions

The fail-closed default may have broken other test classes that build
`EmailChannel`s or post inbound emails. We sweep them now.

**Files:**
- Modify: `apps/channels/tests/test_email_channel.py` — `TestEmailRouting`,
  `TestEmailSessionThreading`, `TestEmailEndToEnd`, `TestHandleEmailMessageTask`
  (and any other class that triggers inbound or saves an `EmailChannelForm`).

- [ ] **Step 1: Run the full email test module to surface failures**

```bash
uv run pytest apps/channels/tests/test_email_channel.py -v
```

Expected: tests in `TestEmailRouting`, `TestEmailSessionThreading`,
`TestEmailEndToEnd`, and `TestHandleEmailMessageTask` may fail because they
either (a) construct an inbound message that flows through
`email_inbound_handler` or (b) save an `EmailChannelForm`. Tests that work
purely with model objects bypassing both code paths will continue to pass.

- [ ] **Step 2: For each failing class, add @override_settings**

For every class whose tests fail because of the new fail-closed default, add a
class-level decorator:

```python
@override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com", "example.com"])
```

Use `["chat.openchatstudio.com", "example.com"]` (covers both the bot-side
addresses and `example.com` participant addresses used in the helpers). If a
class already has another decorator (e.g., `@pytest.mark.django_db()`), the
new decorator goes on a separate line above or below — both work.

For tests that already exercise the rejection path explicitly (none in the
existing suite at the time of writing — but verify), do not override.

- [ ] **Step 3: Re-run the entire email test module**

```bash
uv run pytest apps/channels/tests/test_email_channel.py -v
```

Expected: every test in the module passes.

- [ ] **Step 4: Run the entire channels test suite to catch other affected tests**

```bash
uv run pytest apps/channels/ -v
```

Expected: every test passes. If anything else fails because of the new
setting, apply `@override_settings` at the class or test level as appropriate.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/channels/tests/test_email_channel.py --fix
uv run ruff format apps/channels/tests/test_email_channel.py
```

- [ ] **Step 6: Commit (only if step 2 or 4 made changes)**

```bash
git add apps/channels/tests/test_email_channel.py
git commit -m "test: keep existing email tests passing under fail-closed allowlist default"
```

If no changes were needed, skip the commit.

---

## Task 6: Final verification

- [ ] **Step 1: Run the full project test suite (or at least the channels + forms scope)**

```bash
uv run pytest apps/channels/ apps/experiments/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Run ruff and ty over all modified files**

```bash
uv run ruff check config/settings.py apps/channels/utils.py apps/channels/forms.py apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
uv run ruff format --check config/settings.py apps/channels/utils.py apps/channels/forms.py apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
uv run ty check apps/channels/
```

Expected: no lint or type errors.

- [ ] **Step 3: Manual smoke test the form**

Start the dev server:

```bash
uv run inv runserver
```

In a new shell, set the allowed-domains env var and restart so it loads:

```bash
EMAIL_CHANNEL_ALLOWED_DOMAINS=chat.openchatstudio.com,*.example.com uv run inv runserver
```

Navigate to an experiment, add a new Email channel, and verify:
- Help text under "Email Address" shows: `... Allowed domains: chat.openchatstudio.com, *.example.com.`
- Saving with `support@chat.openchatstudio.com` works.
- Saving with `support@evil.com` shows the inline error mentioning the allowed domains.

Stop the server, restart with `EMAIL_CHANNEL_ALLOWED_DOMAINS=` (empty) and
verify the form help text shows the "No allowed domains are currently
configured" message.

- [ ] **Step 4: Push and open a PR**

Use `.github/pull_request_template.md` for the description. Highlight in the
**Migrations** / risk section that **deploying without setting
`EMAIL_CHANNEL_ALLOWED_DOMAINS` will block all inbound email and prevent
saving email channels** — operators must set the env var before merge rolls
out.

---

## Self-Review Notes

Coverage check vs spec:

| Spec section | Plan task | Notes |
| --- | --- | --- |
| Setting in config/settings.py | Task 1 | Includes `.env.example` doc |
| Helpers `is_email_domain_allowed` / `get_allowed_email_domains` | Task 2 | TDD |
| Inbound pre-filter in `email_inbound_handler` | Task 3 | TDD + retrofitted existing test class |
| Form `clean_email_address` / `clean_from_address` | Task 4 | TDD |
| Form help-text hint (configured + empty cases) | Task 4 | TDD |
| Tests for form (8 cases) | Task 4 | All present |
| Tests for inbound (5 cases) | Task 3 | All present |
| Tests for helper (9 cases) | Task 2 | All present |
| Empty setting fail-closed | Tasks 2, 3, 4 | Tested at every layer |
| Wildcard support | Tasks 2, 3, 4 | Tested at every layer |
| `from_address` validated only when set | Task 4 | `test_from_address_skipped_when_blank` covers it |
| Reply-on-existing-thread still rejected | Task 3 | `test_thread_reply_to_disallowed_domain_still_dropped` |
| Existing tests still pass under fail-closed default | Task 5 | Sweep step |
| Manual smoke test | Task 6 | Step 3 |
| PR description risk callout | Task 6 | Step 4 |

Type / signature consistency:
- `is_email_domain_allowed(email_address: str) -> bool` — used identically in utils, forms, and email_channel.
- `get_allowed_email_domains() -> list[str]` — used identically in forms (help text + error message).
- `_validate_domain(self, address: str, field_name: str) -> str` — defined and used only inside `EmailChannelForm`.

No placeholders. No "implement later" steps. Every code step shows the actual
code to write.

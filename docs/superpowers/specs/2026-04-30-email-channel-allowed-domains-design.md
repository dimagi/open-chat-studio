# Email channel allowed-domains setting

## Goal

Restrict the email channel to a configured set of domains. The list of allowed
domains is stored in a Django setting (env var) and applied in two places:

1. **`EmailChannelForm`** — reject any `email_address` (and `from_address` when
   provided) whose domain is not in the allowed list, and surface the allowed
   domains as a hint in the form help text.
2. **Inbound pre-filter in `email_inbound_handler`** — drop any inbound message
   whose `to_address` domain is not in the allowed list, before enqueueing the
   Celery handler. This protects the default-fallback channel
   (`extra_data.is_default=True`) from accepting mail for domains we don't own.

## Setting

```python
# config/settings.py (add near the existing email block, ~line 408)
EMAIL_CHANNEL_ALLOWED_DOMAINS = env.list("EMAIL_CHANNEL_ALLOWED_DOMAINS", default=[])
```

Document in `.env.example`. Comma-separated. Supports wildcard subdomains
(`*.example.com`) using the same syntax as the embedded-widget allowlist.

**Empty/unset = deny everything (fail-closed).** This is intentional: if an
operator has not configured allowed domains, no inbound mail is processed and
no email channel can be saved. Trying to deploy this change without setting
the env var will block the email feature — call this out in the PR
description.

## Helpers

Add to `apps/channels/utils.py`:

```python
from django.conf import settings


def is_email_domain_allowed(email_address: str) -> bool:
    """True if email_address is on a domain in EMAIL_CHANNEL_ALLOWED_DOMAINS.

    Returns False for malformed addresses (no '@') or when the setting is empty.
    """
    if not email_address or "@" not in email_address:
        return False
    domain = email_address.rsplit("@", 1)[1].lower()
    allowed = settings.EMAIL_CHANNEL_ALLOWED_DOMAINS
    if not allowed:
        return False
    return any(match_domain_pattern(domain, pattern) for pattern in allowed)


def get_allowed_email_domains() -> list[str]:
    """Return the configured allowed-domains list, for UI display."""
    return list(settings.EMAIL_CHANNEL_ALLOWED_DOMAINS)
```

`match_domain_pattern` already exists in this module (`apps/channels/utils.py:16`)
and supports `*.example.com` patterns.

Note: `_domain_from_address` exists in `apps/channels/channels_v2/email_channel.py:151`
but does not lowercase. The new helper lowercases for case-insensitive matching;
no change to the existing helper is needed (callers there use the raw domain
for `make_msgid`).

## Inbound pre-filter

In `apps/channels/channels_v2/email_channel.py`, `email_inbound_handler`
(currently at line 254), add the check after parsing the message and before
the existing has-channel pre-filter:

```python
from apps.channels.utils import is_email_domain_allowed  # module-level import

# ... inside email_inbound_handler, after EmailMessageDatamodel.parse(message):
if not is_email_domain_allowed(email_msg.to_address):
    logger.info(
        "Rejecting inbound email: to-domain not allowed (to=%s)",
        email_msg.to_address,
    )
    return
```

The check applies to **all** inbound emails — including replies on existing
threads. This was an explicit design decision: if a domain is removed from the
allowlist, inflight conversations on that domain stop receiving replies.

## Form changes

In `apps/channels/forms.py` `EmailChannelForm` (currently line 660):

1. **Validators on the address fields** — add `clean_email_address` and
   `clean_from_address` methods that call `is_email_domain_allowed` and raise
   `ValidationError` with a message that lists the allowed domains, e.g.:

   > `"Domain 'foo.com' is not in the allowed list. Allowed: example.com, *.bar.com"`

   `from_address` is only validated when set (it's optional).

   Use `clean_<fieldname>` rather than `clean()` so errors attach to the
   correct field.

2. **Help text hint** — in `__init__`, append the allowed-domains list to the
   `email_address` field's `help_text`. If the list is empty:

   > `"No allowed domains are currently configured. Contact your administrator before saving."`

   If non-empty:

   > `"Allowed domains: example.com, *.bar.com"`

   Append rather than replace so the existing help text is preserved.

3. **No widget changes**, no schema changes, no extra_data changes — purely
   server-side validation plus a help-text update.

## Architecture impact

- No DB migration.
- No template changes (form help text is rendered through the existing
  template).
- No new dependencies.
- `EMAIL_CHANNEL_ALLOWED_DOMAINS` is read at runtime via `settings.` lookup,
  so test overrides via `@override_settings(...)` work normally.

## Edge cases

| Case | Behavior |
| --- | --- |
| Setting empty / unset | All inbound rejected; all form saves blocked. Help text says "No allowed domains configured". |
| Sender address has no `@` | `is_email_domain_allowed` returns False; inbound rejected. |
| Domain casing differs (`Example.com` vs `example.com`) | Compared case-insensitively (lowercase before match). |
| Existing channels with disallowed `email_address` already in DB | Inbound dropped at the pre-filter; existing channels keep loading in the form but cannot be re-saved without fixing the address. (No data migration — operators must reconcile manually.) |
| Wildcard pattern `*.example.com` in setting | Matches `mail.example.com`, does **not** match bare `example.com` (consistent with `match_domain_pattern`). |
| `from_address` on a different domain than `email_address` | Both validated; both must be in the allowed list. |

## Testing

Add to `apps/channels/tests/test_email_channel.py`:

**Form tests** (use `@override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[...])`):

- `email_address` on an exact-match domain → form valid.
- `email_address` matching a `*.example.com` wildcard → form valid.
- `email_address` on a non-allowed domain → `clean_email_address` raises
  `ValidationError` whose message includes the allowed-domain list.
- `from_address` on a non-allowed domain → `clean_from_address` raises.
- Empty setting → `email_address` validation always fails, with the
  "No allowed domains configured" message.
- Help text on a fresh form contains the configured allowed-domain list.
- Help text on an empty setting contains the operator-contact message.

**Inbound tests** (mock `apps.channels.tasks.handle_email_message.delay`):

- `to_address` on an allowed domain → `delay` called once.
- `to_address` matching a wildcard → `delay` called.
- `to_address` on a non-allowed domain → `delay` not called; INFO log emitted.
- `to_address` malformed (no `@`) → `delay` not called.
- Reply on an existing thread (`In-Reply-To` set) to a disallowed
  `to_address` → still dropped (confirms the "applies to all inbound" decision).
- Empty setting → `delay` not called for any to_address.

## Out of scope

- Per-channel or per-team domain allowlists. Single global setting only.
- Sender-side (`from`) validation on inbound. Only `to_address` is checked
  inbound; sender-domain restrictions are explicitly not part of this change.
- Migrating or auto-disabling existing channels whose `email_address` falls
  outside the new allowlist. Operators reconcile manually.
- Auto-reply on rejection. Rejected mail is dropped silently (with a log
  line) — no signal to the sender.

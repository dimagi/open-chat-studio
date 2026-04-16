# Email Channel — Design Document

**Date:** April 2026
**Status:** Draft

## Overview

Add email as a messaging channel in Open Chat Studio. Users communicate with experiments via email. The system receives inbound email through an ESP webhook (AWS SES), processes it through the existing `channels_v2` pipeline, and sends a threaded reply.

Conversation routing follows a Slack-inspired priority model: `In-Reply-To` header for thread continuity, to-address for experiment matching, and a default fallback channel for unsolicited email. Participant identity is the sender email address.

Phase 1 uses a shared OCS-controlled domain (`chat.openchatstudio.com`) for zero per-client infrastructure. Phase 2 (future) adds custom domain provisioning via ESP API.

### Existing infrastructure leveraged

- **django-anymail** — already a dependency; SES configured in production
- **beautifulsoup4** / **html2text** — for HTML parsing (already installed)
- **Celery** — async task processing (already installed)
- **Only new dependency:** `mail-parser-reply` (reply/quote text stripping)

## Goals & Non-Goals

### Goals

- Receive inbound email via ESP webhook and process through the existing pipeline
- Send threaded email replies with correct `In-Reply-To` / `References` headers
- Route messages to the correct experiment using to-address matching with a default fallback
- Strip quoted reply text so the LLM sees only new content
- Self-service channel setup via a shared OCS domain (no per-client infra)
- Participant identified by sender email address
- Follow the same architectural patterns as Telegram, WhatsApp, and Slack channels

### Non-Goals (Phase 1)

- Custom client domains (Phase 2)
- IMAP polling / "bring your own mailbox"
- Microsoft Graph or Gmail API direct integration
- Rich HTML email composition (plain text replies are fine for v1)
- Attachment processing (pass-through to LLM, PDF extraction, etc.)
- Subject-line keyword routing
- Multi-address identity resolution (same person, different email addresses)

## Routing & Identity

### Message Routing

Modeled after Slack's priority chain in `apps/slack/slack_listeners.py:get_experiment_channel()`. Each tier is tried in order; first match wins.

| Priority | Signal | Action | Slack Equivalent |
|----------|--------|--------|------------------|
| **1** | `In-Reply-To` / `References` headers | Look up existing `ExperimentSession` via `external_id`. Skip all further routing. | `thread_ts` → `get_session_for_thread()` |
| **2** | To-address | Match `ExperimentChannel.extra_data["email_address"]`. Create new session. | Exact `slack_channel_id` match |
| **3** | Default fallback | Find channel with `extra_data["is_default"] == True`. Create new session. | `is_default: True` + `SLACK_ALL_CHANNELS` |
| **4** | No match | Ignore the email (no error reply — prevents bounce loops) | *"Unable to find a bot"* |

### Session Continuity

`In-Reply-To` is the primary signal — email clients set it automatically when the user replies. This is the direct analog of Slack's `thread_ts`.

- Store first outbound `Message-ID` as `ExperimentSession.external_id`
- On inbound: look up `In-Reply-To` against `external_id` (unique indexed field)
- Fall back to scanning `References` header — RFC 2822 guarantees root Message-ID is always present
- Same pattern as Slack's `make_session_external_id(channel_id, thread_ts)`

When a user replies to an older message in the thread (not the most recent), `In-Reply-To` may not match `external_id`. However, the `References` header always contains the full thread ancestry including the root Message-ID, so the fallback scan finds it. RFC 2822 specifies that even when `References` is truncated, the first (root) and most recent entries are preserved.

### Participant Identity

Sender email address is the `participant_id`, consistent with how every other channel works:

- Slack: `event["user"]`
- Telegram: `chat.id`
- Email: `from_address` (e.g., `john@example.com`)

Address fuzziness (same person, multiple addresses) is a known limitation accepted for v1.

### Routing Pseudocode

```python
def get_email_experiment_channel(
    in_reply_to: str | None,
    references: list[str],
    to_address: str,
    team: Team,
) -> tuple[ExperimentChannel | None, ExperimentSession | None]:
    """Route an inbound email to the correct channel and session."""

    # Priority 1: Thread continuity via In-Reply-To
    if in_reply_to:
        session = _lookup_session(in_reply_to)
        if session:
            return session.experiment_channel, session

    # Priority 1b: Fallback to References header
    for ref in references:
        session = _lookup_session(ref)
        if session:
            return session.experiment_channel, session

    # Priority 2: To-address match
    channel = ExperimentChannel.objects.filter(
        platform=ChannelPlatform.EMAIL,
        extra_data__contains={"email_address": to_address},
        deleted=False,
    ).select_related("experiment", "team").first()
    if channel:
        return channel, None

    # Priority 3: Default fallback
    default = ExperimentChannel.objects.filter(
        platform=ChannelPlatform.EMAIL,
        extra_data__is_default=True,
        team=team,
        deleted=False,
    ).select_related("experiment", "team").first()
    if default:
        return default, None

    # Priority 4: No match
    return None, None


def _lookup_session(message_id: str) -> ExperimentSession | None:
    """Find a session by its external_id (first outbound Message-ID).

    Uses the existing unique indexed field on ExperimentSession —
    same pattern as Slack's thread lookup via make_session_external_id().
    """
    try:
        return ExperimentSession.objects.select_related(
            "team", "participant", "experiment_channel"
        ).get(external_id=message_id)
    except ExperimentSession.DoesNotExist:
        return None
```

## Domain Strategy

> **MX record constraint:** MX records are domain-wide. Pointing `example.com`'s MX to an ESP redirects *all* mail for that domain. Always use a **subdomain** (e.g., `chat.example.com`) to isolate chatbot email from regular delivery.

### Phase 1 — Shared OCS Domain (implement now)

All email routes through a single OCS-controlled domain. Per-channel addressing handles routing:

- `client-a-support@chat.openchatstudio.com` → Client A's experiment
- `client-b-health@chat.openchatstudio.com` → Client B's experiment

Zero per-client DNS or backend setup. Fully self-service. MX + SPF/DKIM/DMARC configured once.

**Advantages:** No per-client infra, fully self-service, single DNS config.
**Limitations:** Emails come from the OCS domain, not the client's brand.

### Phase 2 — Custom Domains (future)

Automate domain provisioning via ESP API. Client provides subdomain, OCS registers it via SES API, shows required DNS records, polls for verification. Same routing logic — only provisioning changes. SES supports 10K verified domains per region.

## Data Model

### `ChannelPlatform` enum

```python
class ChannelPlatform(models.TextChoices):
    ...
    EMAIL = "email", "Email"              # NEW
```

### `ExperimentChannel.extra_data` schema for email

```json
{
    "email_address": "support@chat.openchatstudio.com",
    "from_address": "noreply@chat.openchatstudio.com",
    "is_default": false
}
```

- `email_address` — the to-address used for routing (Priority 2)
- `from_address` — optional override for the From header on outbound replies (defaults to `settings.DEFAULT_FROM_EMAIL`)
- `is_default` — when `true`, this channel is the fallback for unmatched emails (Priority 3)

### `ExperimentSession.external_id` for thread lookup

Same pattern as Slack. The session's `external_id` stores the first outbound `Message-ID` for the conversation. This field is `CharField(max_length=255, unique=True)` with an existing unique index.

```python
# When creating a new email session:
session = EmailChannel.start_new_session(
    working_experiment=experiment,
    experiment_channel=experiment_channel,
    participant_identifier=sender_email,
    session_external_id=first_outbound_message_id,
    # e.g., "<abc123@chat.openchatstudio.com>"
)

# On inbound reply, lookup is:
ExperimentSession.objects.get(external_id=in_reply_to_value)
```

The `In-Reply-To` header may reference a later message in the thread (not the root). The `References` header always contains the root `Message-ID` per RFC 2822, so we scan it as a fallback. No extra tables or JSON queries needed.

### `EmailMessage` datamodel

New `BaseMessage` subclass in `apps/channels/datamodels.py`, following the same pattern as `TelegramMessage`, `TwilioMessage`, `SlackMessage`:

```python
class EmailMessage(BaseMessage):
    """Inbound email parsed from AnymailInboundMessage."""
    from_address: str
    to_address: str
    subject: str
    message_id: str
    in_reply_to: str | None = None
    references: list[str] = Field(default=[])

    @staticmethod
    def parse(inbound: AnymailInboundMessage) -> "EmailMessage":
        from mail_parser_reply import EmailReplyParser

        body = inbound.text or ""
        reply = EmailReplyParser(languages=["en"]).read(body)
        stripped_text = reply.reply or body

        return EmailMessage(
            participant_id=inbound.from_email.addr_spec,
            message_text=stripped_text,
            from_address=inbound.from_email.addr_spec,
            to_address=inbound.to[0].addr_spec if inbound.to else "",
            subject=inbound.subject or "",
            message_id=inbound.get("Message-ID", ""),
            in_reply_to=inbound.get("In-Reply-To"),
            references=_parse_references(inbound.get("References", "")),
        )


def _parse_references(refs: str) -> list[str]:
    """Parse space-separated Message-ID list from References header."""
    if not refs:
        return []
    return [r.strip() for r in refs.split() if r.strip()]
```

## Inbound Flow

```
ESP Webhook → anymail signal → Parse + Route → Celery Task → Pipeline → EmailSender reply
```

1. **ESP receives email** at the shared domain (e.g., `support@chat.openchatstudio.com`). The MX record routes it to SES. SES delivers to SNS which POSTs to the configured webhook URL.
2. **django-anymail receives the webhook.** Validates the webhook signature. Fires the `anymail.signals.inbound` signal with an `AnymailInboundMessage`.
3. **Signal handler parses and routes.** Creates an `EmailMessage` datamodel (strips quoted text via `mail-parser-reply`). Runs the routing priority chain: `In-Reply-To` → to-address → default. If no match, ignores the email.
4. **Celery task queued.** `handle_email_message.delay(email_data, channel_id, team_id, session_id)`. The view returns `200 OK` immediately (ESP requires fast response).
5. **Pipeline runs.** Task instantiates `EmailChannel`, calls `new_user_message(message)`. The standard pipeline executes: participant validation, session resolution, LLM interaction, response formatting.
6. **EmailSender delivers the reply.** Sends via `django.core.mail.EmailMessage` (which uses the anymail backend). Sets `Message-ID`, `In-Reply-To`, `References`, and `Subject` for proper threading.

> **Why use the anymail signal handler instead of a custom view?** django-anymail already handles webhook signature validation, MIME parsing, and provider normalization. The signal handler receives a clean `AnymailInboundMessage`. No need to duplicate that work.

## Outbound Flow

```python
def send_text(self, text: str, recipient: str) -> None:
    """Send a threaded email reply."""
    msg = EmailMessage(
        subject=self._reply_subject(),
        body=text,
        from_email=self.from_address,
        to=[recipient],
    )

    msg_id = make_msgid(domain=self.domain)
    msg.extra_headers = {
        "Message-ID": msg_id,
    }
    if self.last_message_id:
        msg.extra_headers["In-Reply-To"] = self.last_message_id
        msg.extra_headers["References"] = self._build_references()

    msg.send()

    # The session's external_id already holds the first Message-ID
    # (set at session creation). No per-message storage needed.
```

- **Threading headers:** Every outbound reply sets `In-Reply-To` (parent message) and `References` (root + parent). This ensures Gmail, Outlook, Apple Mail, and Thunderbird all thread correctly.
- **Subject line:** Replies preserve the original subject with `Re:` prefix. Email clients use subject + references for thread grouping. Never change the subject mid-conversation.

## Components

| Component | Type | Extends | Purpose |
|-----------|------|---------|---------|
| `EmailMessage` | Datamodel | `BaseMessage` | Parsed inbound email with threading headers, stripped body text |
| `EmailChannel` | Channel | `ChannelBase` | Pipeline builder. Provides `EmailCallbacks` + `EmailSender`. Text-only. |
| `EmailSender` | Sender | `ChannelSender` | Sends threaded email via django-anymail with `In-Reply-To` / `References` headers |
| `EmailCallbacks` | Callbacks | `ChannelCallbacks` | No-op for v1. Email has no typing indicators or transcription. |
| `EmailChannelForm` | Form | `ExtraFormBase` | Config fields: `email_address`, `from_address`, `is_default` |
| `handle_email_message` | Celery Task | — | Async message handler (same pattern as `handle_telegram_message`) |
| `email_inbound_handler` | Signal Handler | — | Receives anymail `inbound` signal, parses, routes, queues task |
| `get_email_experiment_channel` | Routing Function | — | Priority chain: In-Reply-To → to-address → default |

### `EmailChannel`

```python
class EmailChannel(ChannelBase):
    voice_replies_supported = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(self, experiment, experiment_channel, experiment_session=None,
                 *, email_context=None):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.email_context = email_context  # threading headers from inbound

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()  # no-op for email

    def _get_sender(self) -> EmailSender:
        return EmailSender(
            from_address=self.experiment_channel.extra_data.get(
                "from_address", settings.DEFAULT_FROM_EMAIL
            ),
            email_context=self.email_context,
            experiment_session=self.experiment_session,
        )

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=False,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
        )
```

## Files Changed

| File | Change | Type |
|------|--------|------|
| `apps/channels/models.py` | Add `EMAIL` to `ChannelPlatform`, update `extra_form()`, `channel_identifier_key`, `for_dropdown()` | Modified |
| `apps/channels/forms.py` | Add `EmailChannelForm(ExtraFormBase)` with email_address, from_address, is_default fields + validation | Modified |
| `apps/channels/datamodels.py` | Add `EmailMessage(BaseMessage)` with threading headers and reply parsing | Modified |
| `apps/channels/tasks.py` | Add `handle_email_message` Celery task | Modified |
| `apps/channels/email.py` | Signal handler, routing function, `EmailChannel`, `EmailSender`, `EmailCallbacks` | **New** |
| `apps/channels/apps.py` | Connect the anymail `inbound` signal in `ready()` | Modified |
| `config/settings.py` | Add `ANYMAIL` inbound webhook secret config | Modified |
| `config/urls.py` | Include anymail's inbound URL (if not already present) | Modified |
| `pyproject.toml` | Add `mail-parser-reply` dependency | Modified |
| `apps/channels/tests/test_email_channel.py` | Tests for routing, parsing, sending, threading | **New** |

**No new model fields needed.** Threading uses `ExperimentSession.external_id` (already exists, unique indexed). Only migration required is for the `ChannelPlatform` enum change (adding `"email"` to the `platform` CharField choices).

## Cross-Cutting Concerns

### Reply Parsing

`mail-parser-reply` strips quoted text, signatures, and disclaimers. 13 languages supported. Called during `EmailMessage.parse()` so the LLM only ever sees new content.

### HTML Handling

- **Inbound:** Use `AnymailInboundMessage.text` (plain text part). Fall back to `html2text` on `.html` if no text part.
- **Outbound:** Plain text for v1.

### Spam Prevention

- anymail validates webhook signatures automatically
- Rate limit per sender address
- Check SPF/DKIM pass status
- Sender allowlists/blocklists per experiment (future)

### Email Authentication (SPF, DKIM, DMARC)

Configured once for the shared OCS domain. The ESP handles DKIM signing. DNS records needed:

- **MX**: `chat.openchatstudio.com` → SES inbound endpoint
- **SPF**: TXT record authorizing SES to send on behalf of the domain
- **DKIM**: TXT record with SES-provided public key
- **DMARC**: Start with `p=none`, tighten to `quarantine` after monitoring

### Bounce Loop Prevention

When no matching channel is found (Priority 4), the system **silently ignores** the email. Never send an automated "no bot found" reply — this can trigger infinite bounce loops if the sender is also automated. For legitimate routing failures, log with enough detail to debug.

### Ad Hoc Messages (Reminders, Check-ins)

`ChannelBase.send_message_to_user()` already supports ad hoc bot-initiated messages. `EmailChannel` inherits this. The `EmailSender` sends a new email (not a reply) with a fresh `Message-ID` and subject. If the user replies, the `In-Reply-To` header routes them back into the session.

## Decisions

### Resolved

| # | Question | Decision |
|---|----------|----------|
| 1 | Which ESP? | **AWS SES** — already used in the production instance. |
| 2 | Shared domain? | `chat.openchatstudio.com` as the primary domain. A second domain TBD. |
| 3 | Thread lookup mechanism? | **`ExperimentSession.external_id`** — store first outbound Message-ID. Same pattern as Slack. Unique indexed field, no JSON queries or new tables. |
| 4 | Conversational consent? | **Not supported.** `supports_conversational_consent=False`. |
| 5 | Feature flag? | **Yes** — Waffle flag `flag_email_channel` gates visibility in the channel dropdown, same pattern as `flag_commcare_connect`. |
| 6 | Attachments? | **Phase 2.** Ignore in v1, log their presence for monitoring. |

### Open

| # | Question | Notes |
|---|----------|-------|
| 7 | Second shared domain name | TBD. Will be configured alongside `chat.openchatstudio.com` in SES. |

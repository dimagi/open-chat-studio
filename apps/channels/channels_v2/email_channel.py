from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass, field
from email.utils import make_msgid
from io import BytesIO
from typing import TYPE_CHECKING

import magic
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.db import IntegrityError

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.datamodels import _MAX_REFERENCES, RawAttachment, SkippedAttachment
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import MESSAGE_TYPES
from apps.experiments.models import ExperimentSession
from apps.files.models import File, FilePurpose
from apps.service_providers.file_limits import (
    EMAIL_BLOCKED_CONTENT_TYPES,
    EMAIL_BLOCKED_EXTENSIONS,
    EMAIL_MAX_ATTACHMENT_BYTES,
    EMAIL_TEXT_LIKE_APPLICATION_TYPES,
    can_send_on_email,
)
from apps.teams.utils import set_current_team

if TYPE_CHECKING:
    from apps.experiments.models import Experiment
    from apps.teams.models import Team

logger = logging.getLogger("ocs.channels")

# RFC 2822 reply prefixes across common languages
_REPLY_PREFIX_RE = re.compile(r"^(re|aw|sv|fw|fwd)\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class EmailThreadContext:
    """Threading state passed from inbound email to outbound reply."""

    subject: str = ""
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)

    @classmethod
    def from_inbound(cls, message) -> EmailThreadContext:
        """Build reply threading context from a parsed inbound EmailMessage."""
        subject = message.subject
        if subject and not _REPLY_PREFIX_RE.match(subject):
            subject = f"Re: {subject}"

        references = list(message.references)
        if message.message_id and message.message_id not in references:
            references.append(message.message_id)
        if len(references) > _MAX_REFERENCES:
            references = [references[0], *references[-(_MAX_REFERENCES - 1) :]]

        return cls(
            subject=subject,
            in_reply_to=message.message_id or None,
            references=references,
        )


def get_email_experiment_channel(
    in_reply_to: str | None,
    references: list[str],
    to_address: str,
    sender_address: str | None = None,
    team: Team | None = None,
) -> tuple[ExperimentChannel | None, ExperimentSession | None]:
    """Route an inbound email to the correct channel and session.

    Priority chain (first match wins):
    1. In-Reply-To / References -> existing session lookup (with sender verification)
    2. To-address -> ExperimentChannel.extra_data["email_address"]
    3. Default fallback -> extra_data["is_default"] == True (global, cross-team)
    4. No match -> (None, None)
    """
    # Priority 1: Thread continuity via In-Reply-To
    if in_reply_to:
        session = _lookup_session(in_reply_to, sender_address)
        if session:
            return session.experiment_channel, session

    # Priority 1b: Fallback to References header
    for ref in references:
        session = _lookup_session(ref, sender_address)
        if session:
            return session.experiment_channel, session

    base_filters = {
        "platform": ChannelPlatform.EMAIL,
        "experiment__is_archived": False,
        "deleted": False,
    }

    # Priority 2: To-address match
    channel = (
        ExperimentChannel.objects.filter(
            **base_filters,
            extra_data__contains={"email_address": to_address},
        )
        .select_related("experiment", "team")
        .first()
    )
    if channel:
        return channel, None

    # Priority 3: Default fallback (global — not scoped to team)
    default = (
        ExperimentChannel.objects.filter(
            **base_filters,
            extra_data__contains={"is_default": True},
        )
        .select_related("experiment", "team")
        .first()
    )
    if default:
        return default, None

    # Priority 4: No match
    return None, None


def _lookup_session(message_id: str, sender_address: str | None = None) -> ExperimentSession | None:
    """Find a session by its external_id (first outbound Message-ID).

    If sender_address is provided, verifies the sender matches the session's
    participant to prevent session hijacking via spoofed headers.
    """
    try:
        session = ExperimentSession.objects.select_related("team", "participant", "experiment_channel").get(
            external_id=message_id
        )
    except ExperimentSession.DoesNotExist:
        return None

    if sender_address and session.participant.identifier != sender_address:
        logger.warning(
            "Email sender %s does not match session participant %s for session %s",
            sender_address,
            session.participant.identifier,
            session.id,
        )
        return None

    return session


def _has_email_message_id(external_id) -> bool:
    """Check if an external_id is an RFC 5322 Message-ID (angle-bracket-delimited)."""
    return str(external_id).startswith("<")


def _domain_from_address(email_address: str) -> str:
    """Extract the domain part from an email address."""
    if "@" in email_address:
        return email_address.rsplit("@", 1)[1]
    return email_address


def _detect_content_type(content: bytes, fallback: str = "") -> str:
    try:
        detected = magic.from_buffer(content[:2048], mime=True)
        if detected and detected != "application/octet-stream":
            return detected
    except Exception:
        logger.exception("magic content-type detection failed")
    return fallback or "application/octet-stream"


def _category(content_type: str) -> str:
    """Top-level category for mismatch comparison.
    Maps known textual application/* types (JSON, XML, YAML, ...) to 'text'
    since magic typically returns text/plain for them.
    """
    if content_type in EMAIL_TEXT_LIKE_APPLICATION_TYPES:
        return "text"
    return content_type.split("/", 1)[0]


def _is_blocked(extension: str, claimed_type: str, detected_type: str) -> str | None:
    """Returns a rejection reason if blocked, else None."""
    if extension in EMAIL_BLOCKED_EXTENSIONS:
        return f"file extension '.{extension}' not allowed"
    if detected_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (detected: {detected_type})"
    if claimed_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (claimed: {claimed_type})"
    if (
        claimed_type
        and claimed_type != "application/octet-stream"
        and detected_type != "application/octet-stream"
        and _category(claimed_type) != _category(detected_type)
    ):
        return f"content type mismatch (claimed: {claimed_type}, detected: {detected_type})"
    return None


def _persist_inbound_attachments(raw: list[RawAttachment], team_id: int) -> tuple[list[int], list[dict]]:
    """Filter and save inbound email attachments, returning accepted File IDs
    and skipped-attachment metadata for surfacing to the LLM."""
    accepted_ids: list[int] = []
    skipped: list[dict] = []
    for att in raw:
        size = len(att.content_bytes)
        ext = pathlib.Path(att.filename or "").suffix.lstrip(".").lower()
        detected = _detect_content_type(att.content_bytes, fallback=att.content_type)

        if reason := _is_blocked(ext, att.content_type, detected):
            skipped.append({"name": att.filename, "reason": reason, "size": size})
            continue
        if size > EMAIL_MAX_ATTACHMENT_BYTES:
            skipped.append({"name": att.filename, "reason": "exceeds 20 MB limit", "size": size})
            continue

        try:
            f = File.create(
                filename=att.filename or "attachment",
                file_obj=BytesIO(att.content_bytes),
                team_id=team_id,
                purpose=FilePurpose.MESSAGE_MEDIA,
                content_type=detected,
            )
        except Exception:
            logger.exception("Failed to persist inbound email attachment %r", att.filename)
            skipped.append({"name": att.filename, "reason": "storage error", "size": size})
            continue
        accepted_ids.append(f.id)

    return accepted_ids, skipped


class EmailSender(ChannelSender):
    """Sends threaded email replies via django.core.mail.

    Buffers the body text and file attachments across send_text / send_file
    calls and commits them as a single Django EmailMessage in flush().  This
    ensures text + attachments arrive as one threaded reply instead of
    separate messages.
    """

    def __init__(
        self,
        from_address: str,
        domain: str,
        thread_context: EmailThreadContext | None = None,
    ):
        self.from_address = from_address
        self.domain = domain
        self.thread_context = thread_context or EmailThreadContext()
        self.last_message_id: str | None = None
        self._body: str = ""
        self._recipient: str = ""
        # Each entry is a (filename, content_bytes, mimetype) 3-tuple as
        # accepted by Django's EmailMessage.attach().
        self._attachments: list[tuple[str, bytes, str]] = []

    def send_text(self, text: str, recipient: str) -> None:
        """Stage the body text.  The email is not sent until flush() is called."""
        self._body = text
        self._recipient = recipient

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        """Stage a file attachment.  The email is not sent until flush() is called."""
        self._recipient = recipient
        with file.file.open("rb") as fh:
            content = fh.read()
        self._attachments.append((file.name, content, file.content_type or "application/octet-stream"))

    def flush(self) -> None:
        """Build and send the buffered email, then reset internal state."""
        if not self._body and not self._attachments:
            return

        ctx = self.thread_context
        msg_id = make_msgid(domain=self.domain)

        msg = DjangoEmailMessage(
            subject=ctx.subject or "New message",
            body=self._body,
            from_email=self.from_address,
            to=[self._recipient],
        )
        msg.extra_headers = {"Message-ID": msg_id}

        if ctx.in_reply_to:
            msg.extra_headers["In-Reply-To"] = ctx.in_reply_to
            msg.extra_headers["References"] = " ".join(ctx.references)

        for filename, content, mimetype in self._attachments:
            msg.attach(filename, content, mimetype)

        msg.send()
        self.last_message_id = msg_id

        # Reset buffer so subsequent flush() after more sends produces a new email.
        self._body = ""
        self._recipient = ""
        self._attachments = []


class EmailChannel(ChannelBase):
    """Email channel -- text-only, no voice, no conversational consent."""

    voice_replies_supported = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        *,
        thread_context: EmailThreadContext | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.thread_context = thread_context or EmailThreadContext()
        self._sender_instance: EmailSender | None = None

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> EmailSender:
        extra = self.experiment_channel.extra_data
        email_address = extra.get("email_address", "")
        self._sender_instance = EmailSender(
            from_address=extra.get("from_address") or email_address,
            domain=_domain_from_address(email_address),
            thread_context=self.thread_context,
        )
        return self._sender_instance

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=True,
            supports_conversational_consent=False,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        return can_send_on_email(file.content_type, file.content_size).supported

    def new_user_message(self, message):
        response = super().new_user_message(message)

        # After pipeline: capture outbound Message-ID for thread continuity.
        # The session's external_id defaults to a UUID. If the sender produced
        # a Message-ID, store it so future In-Reply-To lookups find this session.
        if self.experiment_session and self._sender_instance:
            msg_id = self._sender_instance.last_message_id
            if msg_id and not _has_email_message_id(self.experiment_session.external_id):
                self.experiment_session.external_id = msg_id  # ty: ignore[invalid-assignment]
                try:
                    self.experiment_session.save(update_fields=["external_id"])
                except IntegrityError:
                    logger.warning(
                        "Could not save Message-ID %s as external_id for session %s (duplicate)",
                        msg_id,
                        self.experiment_session.id,
                    )

        return response


def email_inbound_handler(sender, message, event, **kwargs):
    """Handle inbound email from anymail's inbound signal.

    Performs full routing here (not in the Celery task) so attachments
    can be persisted to the team's File storage with team context known.
    The Celery payload carries File IDs, never raw bytes.

    Returns immediately so the ESP gets a fast 200 OK.
    """
    from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel  # noqa: PLC0415
    from apps.channels.tasks import handle_email_message  # noqa: PLC0415

    if getattr(message, "spam_detected", None) is True:
        logger.info("Discarding spam email from %s", getattr(message, "from_email", "unknown"))
        return

    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    channel, session = get_email_experiment_channel(
        in_reply_to=email_msg.in_reply_to,
        references=email_msg.references,
        to_address=email_msg.to_address,
        sender_address=email_msg.from_address,
    )
    if not channel:
        logger.info("No email channel found for to=%s, ignoring", email_msg.to_address)
        return

    set_current_team(channel.team)

    accepted_ids: list[int] = []
    skipped: list[dict] = []
    try:
        accepted_ids, skipped = _persist_inbound_attachments(email_msg._raw_attachments, team_id=channel.team_id)
    except Exception:
        logger.exception("Top-level failure persisting inbound attachments; proceeding with text only")

    email_msg.attachment_file_ids = accepted_ids
    email_msg.skipped_attachments = [SkippedAttachment(**s) for s in skipped]
    if skipped:
        email_msg.message_text = _augment_with_skip_notes(email_msg.message_text, skipped)

    handle_email_message.delay(
        email_data=email_msg.model_dump(),
        channel_id=channel.id,
        session_id=session.id if session else None,
    )


def _augment_with_skip_notes(message_text: str, skipped: list[dict]) -> str:
    """Append one bracketed line per skipped attachment to message_text so
    the LLM can surface the skip reasons to the user."""
    if not skipped:
        return message_text
    lines = [f"[Attachment {s['name']!r} ({_human_size(s['size'])}) skipped — {s['reason']}]" for s in skipped]
    suffix = "\n\n" + "\n".join(lines)
    return (message_text or "").rstrip() + suffix


def _human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "size unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

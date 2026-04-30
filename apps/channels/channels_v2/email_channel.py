from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from email.utils import make_msgid
from typing import TYPE_CHECKING

from django.core.mail import EmailMessage as DjangoEmailMessage
from django.db import IntegrityError

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import is_email_domain_allowed
from apps.chat.channels import MESSAGE_TYPES
from apps.experiments.models import ExperimentSession

if TYPE_CHECKING:
    from apps.experiments.models import Experiment
    from apps.teams.models import Team

logger = logging.getLogger("ocs.channels")

_MAX_REFERENCES = 50

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


class EmailSender(ChannelSender):
    """Sends threaded email replies via django.core.mail."""

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

    def send_text(self, text: str, recipient: str) -> None:
        ctx = self.thread_context
        msg = DjangoEmailMessage(
            subject=ctx.subject or "New message",
            body=text,
            from_email=self.from_address,
            to=[recipient],
        )

        msg_id = make_msgid(domain=self.domain)
        msg.extra_headers = {"Message-ID": msg_id}

        if ctx.in_reply_to:
            msg.extra_headers["In-Reply-To"] = ctx.in_reply_to
            msg.extra_headers["References"] = " ".join(ctx.references)

        msg.send()
        self.last_message_id = msg_id


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
            supports_files=False,
            supports_conversational_consent=False,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
        )

    def new_user_message(self, message):
        response = super().new_user_message(message)

        # After pipeline: capture outbound Message-ID for thread continuity.
        # The session's external_id defaults to a UUID. If the sender produced
        # a Message-ID, store it so future In-Reply-To lookups find this session.
        if self.experiment_session and self._sender_instance:
            msg_id = self._sender_instance.last_message_id
            if msg_id and not _has_email_message_id(self.experiment_session.external_id):
                self.experiment_session.external_id = msg_id
                try:
                    self.experiment_session.save(update_fields=["external_id"])
                except IntegrityError:
                    logger.warning(
                        "Could not save Message-ID %s as external_id for session %s (duplicate)",
                        msg_id,
                        self.experiment_session.id,
                    )

        return response


def email_inbound_handler(sender, event, **kwargs):
    """Handle inbound email from anymail's inbound signal.

    Parses the email and enqueues a Celery task for async processing.
    Returns immediately so the ESP gets a fast 200 OK.

    The pre-filter here is intentionally lenient — it only checks whether
    any email channel exists that could plausibly handle this message.
    The full routing logic lives in get_email_experiment_channel (called
    by the Celery task).
    """
    from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel  # noqa: PLC0415
    from apps.channels.tasks import handle_email_message  # noqa: PLC0415

    message = event.message

    # Check ESP spam verdict before processing
    if getattr(message, "spam_detected", None) is True:
        logger.info("Discarding spam email from %s", getattr(message, "from_email", "unknown"))
        return

    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    if not is_email_domain_allowed(email_msg.to_address):
        logger.info(
            "Rejecting inbound email: to-domain not allowed (to=%s, in_reply_to=%s)",
            email_msg.to_address,
            email_msg.in_reply_to or "-",
        )
        return

    # Best-effort pre-filter: enqueue if any email channel could handle this.
    # Check thread continuity (In-Reply-To or References), to-address, or default.
    has_existing_session = False
    message_ids_to_check = []
    if email_msg.in_reply_to:
        message_ids_to_check.append(email_msg.in_reply_to)
    message_ids_to_check.extend(email_msg.references)

    if message_ids_to_check:
        has_existing_session = ExperimentSession.objects.filter(
            external_id__in=message_ids_to_check,
            experiment_channel__platform=ChannelPlatform.EMAIL,
        ).exists()

    if not has_existing_session:
        has_channel = ExperimentChannel.objects.filter(
            platform=ChannelPlatform.EMAIL,
            deleted=False,
        ).exists()
        if not has_channel:
            logger.info("No email channel found for to=%s, ignoring", email_msg.to_address)
            return

    handle_email_message.delay(email_data=email_msg.model_dump())

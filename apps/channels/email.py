from __future__ import annotations

import logging
from email.utils import make_msgid
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import MESSAGE_TYPES
from apps.experiments.models import ExperimentSession

if TYPE_CHECKING:
    from apps.experiments.models import Experiment
    from apps.teams.models import Team

logger = logging.getLogger("ocs.channels")


def get_email_experiment_channel(
    in_reply_to: str | None,
    references: list[str],
    to_address: str,
    team: Team | None = None,
) -> tuple[ExperimentChannel | None, ExperimentSession | None]:
    """Route an inbound email to the correct channel and session.

    Priority chain (first match wins):
    1. In-Reply-To / References -> existing session lookup
    2. To-address -> ExperimentChannel.extra_data["email_address"]
    3. Default fallback -> extra_data["is_default"] == True (requires team)
    4. No match -> (None, None)
    """
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
    channel = (
        ExperimentChannel.objects.filter(
            platform=ChannelPlatform.EMAIL,
            extra_data__contains={"email_address": to_address},
            deleted=False,
        )
        .select_related("experiment", "team")
        .first()
    )
    if channel:
        return channel, None

    # Priority 3: Default fallback (only if team is known)
    if team:
        default = (
            ExperimentChannel.objects.filter(
                platform=ChannelPlatform.EMAIL,
                extra_data__contains={"is_default": True},
                team=team,
                deleted=False,
            )
            .select_related("experiment", "team")
            .first()
        )
        if default:
            return default, None

    # Priority 4: No match
    return None, None


def _lookup_session(message_id: str) -> ExperimentSession | None:
    """Find a session by its external_id (first outbound Message-ID)."""
    try:
        return ExperimentSession.objects.select_related("team", "participant", "experiment_channel").get(
            external_id=message_id
        )
    except ExperimentSession.DoesNotExist:
        return None


class EmailSender(ChannelSender):
    """Sends threaded email replies via django.core.mail."""

    def __init__(
        self,
        from_address: str,
        domain: str,
        subject: str = "",
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ):
        self.from_address = from_address
        self.domain = domain
        self.subject = subject
        self.in_reply_to = in_reply_to
        self.references = references or []
        self.last_message_id: str | None = None

    def send_text(self, text: str, recipient: str) -> None:
        msg = DjangoEmailMessage(
            subject=self.subject or "New message",
            body=text,
            from_email=self.from_address,
            to=[recipient],
        )

        msg_id = make_msgid(domain=self.domain)
        msg.extra_headers = {"Message-ID": msg_id}

        if self.in_reply_to:
            msg.extra_headers["In-Reply-To"] = self.in_reply_to
            msg.extra_headers["References"] = " ".join(self.references + [self.in_reply_to])

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
        email_context: dict | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.email_context = email_context or {}
        self._sender_instance: EmailSender | None = None

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> EmailSender:
        extra = self.experiment_channel.extra_data
        self._sender_instance = EmailSender(
            from_address=extra.get("from_address") or settings.DEFAULT_FROM_EMAIL,
            domain=settings.EMAIL_CHANNEL_DOMAIN,
            subject=self.email_context.get("subject", ""),
            in_reply_to=self.email_context.get("in_reply_to"),
            references=self.email_context.get("references", []),
        )
        return self._sender_instance

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=False,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
        )


def email_inbound_handler(sender, message, event, **kwargs):
    """Handle inbound email from anymail's inbound signal.

    Parses the email, routes it, and enqueues a Celery task.
    Returns immediately so the ESP gets a fast 200 OK.
    """
    from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel  # noqa: PLC0415
    from apps.channels.tasks import handle_email_message  # noqa: PLC0415

    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    experiment_channel, session = get_email_experiment_channel(
        in_reply_to=email_msg.in_reply_to,
        references=email_msg.references,
        to_address=email_msg.to_address,
        team=None,
    )

    if not experiment_channel:
        logger.info("No email channel found for to=%s, ignoring", email_msg.to_address)
        return

    handle_email_message.delay(
        email_data=email_msg.model_dump(),
        channel_id=experiment_channel.id,
        session_id=session.id if session else None,
    )

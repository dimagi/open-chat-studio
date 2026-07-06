from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.files.models import File


def _default_can_send_file(_file: File) -> bool:
    return False


@dataclass(frozen=True)
class PlatformConsentConfig:
    """Platform consent gate read by ConsentCheckStage.

    Distinct from the conversational consent flow (``ConsentFlowStage``):
    this enforces a platform-level consent flag stored in
    ``ParticipantData.system_metadata`` (e.g. CommCare Connect's auto-consent
    handshake, or Telegram revoking consent when the bot is blocked).

    Only channels whose platform maintains such a flag configure this. When
    unset (``ChannelCapabilities.consent_config is None``) the stage is
    skipped.

    ``strict=True``: abort when no ParticipantData row exists.
    ``default_consent``: value used when the row exists but has no
    ``consent`` key in ``system_metadata`` -- True lets unspecified
    participants through, False blocks them.

    When the gate blocks, the pipeline aborts silently (EarlyAbort);
    no user-facing message is sent.
    """

    strict: bool = False
    default_consent: bool = True


@dataclass(frozen=True)
class ChannelCapabilities:
    """Describes what a channel can do. Populated at runtime — either from
    static ClassVars (Telegram) or from the messaging service (WhatsApp)."""

    supports_voice_replies: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supports_static_triggers: bool = True
    supported_message_types: tuple[str, ...] = field(default_factory=tuple)
    # File-level checking is delegated to a callable so that channel-specific
    # size/mime rules don't leak into the capabilities dataclass.
    can_send_file: Callable[[File], bool] = _default_can_send_file
    # When set, ConsentCheckStage runs immediately after SessionResolutionStage
    # and raises EarlyAbort if the participant has not consented.
    consent_config: PlatformConsentConfig | None = None

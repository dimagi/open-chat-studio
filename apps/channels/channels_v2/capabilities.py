from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.files.models import File


def _default_can_send_file(_file: File) -> bool:
    return False


@dataclass(frozen=True)
class ConsentConfig:
    """Platform consent gate read by ConsentCheckStage.

    Only channels whose platform maintains a ParticipantData consent flag
    (currently CommCare Connect and Telegram) configure this. When unset
    (``ChannelCapabilities.consent_config is None``) the stage is skipped.

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
    # When set, ConsentCheckStage runs after SessionActivationStage and
    # raises EarlyAbort if the participant has not consented.
    consent_config: ConsentConfig | None = None

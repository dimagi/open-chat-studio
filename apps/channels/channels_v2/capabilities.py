from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.files.models import File


def _default_can_send_file(_file: File) -> bool:
    return False


@dataclass(frozen=True)
class ChannelCapabilities:
    """Describes what a channel can do. Populated at runtime — either from
    static ClassVars (Telegram) or from the messaging service (WhatsApp)."""

    supports_voice: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supports_static_triggers: bool = True
    supported_message_types: tuple[str, ...] = field(default_factory=tuple)
    # File-level checking is delegated to a callable so that channel-specific
    # size/mime rules don't leak into the capabilities dataclass.
    can_send_file: Callable[[File], bool] = _default_can_send_file

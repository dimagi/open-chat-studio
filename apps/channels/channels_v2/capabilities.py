from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelCapabilities:
    """Describes what a channel can do. Populated at runtime — either from
    static ClassVars (Telegram) or from the messaging service (WhatsApp)."""

    supports_voice_replies: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supports_static_triggers: bool = True
    supported_message_types: list = field(default_factory=list)
    # File-level checking is delegated to a callable so that channel-specific
    # size/mime rules don't leak into the capabilities dataclass.
    can_send_file: callable = lambda file: False  # (File) -> bool

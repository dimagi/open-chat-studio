"""Intron.io TTS constants and voice-seeding helpers.

Intron exposes a fixed catalogue of (accent, gender) pairs rather than a
voice-discovery endpoint, so we seed SyntheticVoice rows from the static
list below whenever a provider is created.
"""

from typing import TYPE_CHECKING

from apps.experiments.models import SyntheticVoice

if TYPE_CHECKING:
    from apps.service_providers.models import VoiceProvider

INTRON_BASE_URL = "https://infer.voice.intron.io"

ACCENTS: tuple[str, ...] = (
    "afrikaans",
    "akan",
    "amharic",
    "arabic",
    "bajju",
    "bekwarra",
    "benin",
    "bette",
    "chichewa",
    "ebira",
    "eggon",
    "epie",
    "estako",
    "french",
    "fulani",
    "ga",
    "gerawa",
    "hausa",
    "ibibio",
    "idoma",
    "igala",
    "igbo",
    "ijaw",
    "isindebele",
    "isoko",
    "kanuri",
    "kinyarwanda",
    "luganda",
    "nupe",
    "nyandang",
    "ogbia",
    "ogoni",
    "pidgin",
    "sepedi",
    "sesotho",
    "shona",
    "siswati",
    "swahili",
    "tiv",
    "tswana",
    "twi",
    "urhobo",
    "xhosa",
    "yoruba",
    "zulu",
)

GENDERS: tuple[str, ...] = ("male", "female")


def build_intron_synthetic_voices(provider: "VoiceProvider") -> None:
    """Create one SyntheticVoice per (accent, gender) for `provider`.

    Idempotent: `ignore_conflicts=True` relies on the (name, gender, service,
    voice_provider) unique index so re-running against an already-seeded provider
    is a no-op. Defaults are deterministic from the tuple, so there's nothing to
    refresh on the existing rows.
    """
    voices = [
        SyntheticVoice(
            name=accent,
            gender=gender,
            service=SyntheticVoice.Intron,
            voice_provider=provider,
            neural=True,
            language=accent.capitalize(),
            language_code=accent,
        )
        for accent in ACCENTS
        for gender in GENDERS
    ]
    SyntheticVoice.objects.bulk_create(voices, ignore_conflicts=True)

"""MiniMax T2A (text-to-speech) constants and voice-seeding helpers.

MiniMax exposes a fixed catalogue of built-in "system" voices addressed by a
``voice_id`` rather than a voice-discovery endpoint, so we seed SyntheticVoice
rows from the curated list below whenever a provider is created (mirroring the
Intron provider). The list is a subset of MiniMax's published English system
voices with unambiguous genders; more can be added here, or a user can supply a
custom cloned ``voice_id``.

See: https://platform.minimax.io/docs/faq/system-voice-id
"""

from typing import TYPE_CHECKING

from field_audit.models import AuditAction

from apps.experiments.models import SyntheticVoice

if TYPE_CHECKING:
    from apps.service_providers.models import VoiceProvider

MINIMAX_BASE_URL = "https://api.minimax.io"

# Default T2A model; see https://platform.minimax.io/docs/guides/models-intro
DEFAULT_MINIMAX_TTS_MODEL = "speech-2.8-hd"

# (voice_id, display name, gender) for a curated set of MiniMax's built-in
# English system voices. voice_id values are verbatim from MiniMax's docs.
SYSTEM_VOICES: tuple[tuple[str, str, str], ...] = (
    ("English_Graceful_Lady", "Graceful Lady", "female"),
    ("English_radiant_girl", "Radiant Girl", "female"),
    ("English_CalmWoman", "Calm Woman", "female"),
    ("English_ConfidentWoman", "Confident Woman", "female"),
    ("English_Wiselady", "Wise Lady", "female"),
    ("English_Kind-heartedGirl", "Kind-hearted Girl", "female"),
    ("English_Trustworth_Man", "Trustworthy Man", "male"),
    ("English_Diligent_Man", "Diligent Man", "male"),
    ("English_ManWithDeepVoice", "Man With Deep Voice", "male"),
    ("English_PatientMan", "Patient Man", "male"),
    ("English_DecentYoungMan", "Decent Young Man", "male"),
    ("English_Steadymentor", "Steady Mentor", "male"),
)


def build_minimax_synthetic_voices(provider: "VoiceProvider") -> None:
    """Create one SyntheticVoice per built-in system voice for `provider`.

    The API-facing ``voice_id`` is stored in ``external_id``. Idempotent:
    because ``external_id`` is set, ``ignore_conflicts=True`` deduplicates against
    the ``unique_external_id_per_service_provider`` constraint on
    ``(external_id, service, voice_provider)``, so re-running against an
    already-seeded provider is a no-op.
    """
    voices = [
        SyntheticVoice(
            name=display_name,
            external_id=voice_id,
            gender=gender,
            service=SyntheticVoice.MiniMax,
            voice_provider=provider,
            neural=True,
            language="English",
            language_code="en",
        )
        for voice_id, display_name, gender in SYSTEM_VOICES
    ]
    SyntheticVoice.objects.bulk_create(voices, ignore_conflicts=True, audit_action=AuditAction.AUDIT)

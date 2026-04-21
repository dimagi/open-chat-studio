import pytest

from apps.experiments.models import SyntheticVoice
from apps.service_providers.intron import ACCENTS, build_synthetic_voices
from apps.service_providers.models import VoiceProviderType
from apps.utils.factories.service_provider_factories import VoiceProviderFactory


def test_accents_matches_spec_exactly():
    """The accent catalogue must match the set enumerated in issue #2966 exactly.

    Using set equality (not issubset) catches both additions and removals, so an accidental
    change to the catalogue surfaces immediately.
    """
    expected = {
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
    }
    assert set(ACCENTS) == expected


@pytest.mark.django_db()
def test_build_synthetic_voices_creates_one_per_accent_and_gender(team_with_users):
    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.intron,
        config={"intron_api_key": "test_key"},
    )
    build_synthetic_voices(provider)
    voices = provider.syntheticvoice_set.all()
    assert voices.count() == len(ACCENTS) * 2
    yoruba_voices = voices.filter(name="yoruba")
    assert yoruba_voices.count() == 2
    assert set(yoruba_voices.values_list("gender", flat=True)) == {"male", "female"}
    assert all(v.service == SyntheticVoice.Intron for v in yoruba_voices)
    assert all(v.voice_provider_id == provider.id for v in yoruba_voices)


@pytest.mark.django_db()
def test_build_synthetic_voices_idempotent(team_with_users):
    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.intron,
        config={"intron_api_key": "test_key"},
    )
    build_synthetic_voices(provider)
    original = provider.syntheticvoice_set.count()
    build_synthetic_voices(provider)
    assert provider.syntheticvoice_set.count() == original

import pytest
from django.urls import reverse

from apps.experiments.models import SyntheticVoice
from apps.service_providers.intron import ACCENTS, build_synthetic_voices
from apps.service_providers.models import VoiceProvider, VoiceProviderType
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


@pytest.mark.django_db()
def test_run_post_save_hook_seeds_intron_voices(team_with_users):
    """Intron post-save hook creates 90 SyntheticVoice rows and returns no warnings."""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="Intron Test",
        type=VoiceProviderType.intron,
        config={"intron_api_key": "test_key"},
    )
    warnings = provider.run_post_save_hook()
    assert warnings == []
    assert provider.syntheticvoice_set.count() == len(ACCENTS) * 2


@pytest.mark.django_db()
def test_run_post_save_hook_returns_warning_on_seeding_failure(team_with_users):
    """If build_synthetic_voices raises, the hook logs and returns a user-facing warning."""
    from unittest import mock  # noqa: PLC0415 - only needed in this test

    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="Intron Test",
        type=VoiceProviderType.intron,
        config={"intron_api_key": "test_key"},
    )
    # Patch the bound name in models (where run_post_save_hook resolves it), not intron.
    with mock.patch(
        "apps.service_providers.models.build_synthetic_voices",
        side_effect=RuntimeError("boom"),
    ):
        warnings = provider.run_post_save_hook()

    assert len(warnings) == 1
    assert "voice seeding failed" in warnings[0].lower()
    assert provider.syntheticvoice_set.count() == 0


@pytest.mark.django_db()
def test_create_intron_provider_via_view_seeds_voices(team_with_users, client):
    """Creating an intron provider via the view seeds voices end-to-end.

    Form field convention: secondary forms in BaseTypeSelectFormView are instantiated without
    a Django form prefix (confirmed by reading apps/service_providers/utils.py:93 and
    apps/generics/type_select_form.py). Fields are submitted bare (e.g. 'intron_api_key'),
    not prefixed (e.g. 'intron-intron_api_key').
    """
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:new",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "voice"},
    )
    data = {
        "type": VoiceProviderType.intron.value,
        "name": "My Intron",
        "intron_api_key": "test_key",
    }
    response = client.post(url, data=data, follow=True)
    assert response.status_code == 200
    provider = VoiceProvider.objects.get(team=team_with_users, name="My Intron")
    assert provider.syntheticvoice_set.count() == len(ACCENTS) * 2

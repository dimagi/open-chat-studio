from io import BytesIO
from unittest import mock

import factory
import pytest
from django.db import IntegrityError

from apps.experiments.models import SyntheticVoice
from apps.files.models import File
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import VoiceProvider, VoiceProviderType, _map_elevenlabs_gender
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory


def test_aws_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.aws,
        data={
            "aws_access_key_id": "test_key",
            "aws_secret_access_key": "test_secret",
            "aws_region": "test_region",
        },
    )


@pytest.mark.parametrize(
    "config_key",
    [
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_region",
    ],
)
def test_aws_voice_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = VoiceProviderType.aws.form_cls(
        team=None,
        data={
            "aws_access_key_id": "test_key",
            "aws_secret_access_key": "test_secret",
            "aws_region": "test_region",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.aws, data=form.cleaned_data)


def test_azure_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.azure,
        data={
            "azure_subscription_key": "test_key",
            "azure_region": "test_region",
        },
    )


@pytest.mark.parametrize(
    "config_key",
    [
        "azure_subscription_key",
        "azure_region",
    ],
)
def test_azure_voice_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = VoiceProviderType.azure.form_cls(
        team=None,
        data={
            "azure_subscription_key": "test_key",
            "azure_region": "test_region",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.azure, data=form.cleaned_data)


def _test_voice_provider_error(provider_type: VoiceProviderType, data):
    form = provider_type.form_cls(team=None, data=data)
    assert not form.is_valid()

    with pytest.raises(ServiceProviderConfigError):
        provider_type.get_speech_service(config=data)


def _test_voice_provider(team, provider_type: VoiceProviderType, data):
    form = provider_type.form_cls(team, data=data)
    assert form.is_valid()
    provider = VoiceProvider.objects.create(
        team=team,
        name=f"{provider_type} Test Provider",
        type=provider_type,
        config=form.cleaned_data,
    )

    service = {
        VoiceProviderType.aws: SyntheticVoice.AWS,
        VoiceProviderType.azure: SyntheticVoice.Azure,
        VoiceProviderType.openai: SyntheticVoice.OpenAI,
        VoiceProviderType.openai_voice_engine: SyntheticVoice.OpenAIVoiceEngine,
        VoiceProviderType.elevenlabs: SyntheticVoice.ElevenLabs,
    }[provider_type]
    voice = SyntheticVoice(
        name="test", neural=True, language="English", language_code="en", gender="female", service=service
    )

    speech_service = provider.get_speech_service()
    # bypass pydantic validation
    mock_synthesize = mock.Mock(return_value=(SynthesizedAudio(audio=None, duration=0.0, format="mp3")))
    object.__setattr__(speech_service, "_synthesize_voice", mock_synthesize)
    speech_service.synthesize_voice("test", voice)
    assert mock_synthesize.call_count == 1
    return provider


def test_openai_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.openai,
        data={
            "openai_api_key": "test_key",
            "openai_api_base": "https://openai.com",
            "openai_organization": "test_organization",
        },
    )


@pytest.mark.parametrize(
    "config_key",
    [
        "openai_api_key",
    ],
)
def test_openai_voice_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = VoiceProviderType.openai.form_cls(
        team=None,
        data={
            "openai_api_key": "test_key",
            "openai_api_base": "https://openai.com",
            "openai_organization": "test_organization",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.openai, data=form.cleaned_data)


@pytest.mark.django_db()
def test_synthetic_voice_external_id(team_with_users):
    """SyntheticVoice should support an external_id field for opaque provider voice identifiers"""

    provider = VoiceProviderFactory(team=team_with_users)
    voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="21m00Tcm4TlvDq8ikWAM",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    voice.refresh_from_db()
    assert voice.external_id == "21m00Tcm4TlvDq8ikWAM"
    assert voice.name == "Rachel"


@pytest.mark.django_db()
def test_synthetic_voice_external_id_uniqueness(team_with_users):
    """Two voices with the same external_id, service, and voice_provider should be rejected"""
    provider = VoiceProviderFactory(team=team_with_users)
    SyntheticVoice.objects.create(
        name="Rachel",
        external_id="21m00Tcm4TlvDq8ikWAM",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    with pytest.raises(IntegrityError):
        SyntheticVoice.objects.create(
            name="Rachel Clone",
            external_id="21m00Tcm4TlvDq8ikWAM",
            neural=True,
            language="English",
            language_code="en",
            gender="female",
            service=SyntheticVoice.ElevenLabs,
            voice_provider=provider,
        )


def test_openai_ve_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.openai_voice_engine,
        data={
            "openai_api_key": "test_key",
            "openai_api_base": "https://openai.com",
            "openai_organization": "test_organization",
        },
    )


def test_openai_ve_provider_delete(team_with_users):
    """Deleting the voice provider should remove all associated synthetic voices and files as well"""
    provider = _test_voice_provider(
        team_with_users,
        VoiceProviderType.openai_voice_engine,
        data={
            "openai_api_key": "test_key",
            "openai_api_base": "https://openai.com",
            "openai_organization": "test_organization",
        },
    )

    files = FileFactory.create_batch(3, name=factory.Sequence(lambda n: f"file_{n + 1}.mp3"))
    provider.add_files(files)
    synthetic_voices = provider.syntheticvoice_set.all()
    files = [voice.file for voice in synthetic_voices]
    assert len(synthetic_voices) == 3
    assert len(files) == 3

    provider.delete()
    for voice in synthetic_voices:
        with pytest.raises(SyntheticVoice.DoesNotExist):
            # These synthetic voices should be deleted along with the provider, meaning DoesNotExist will be
            # raised when we refresh them
            voice.refresh_from_db()

    for file in files:
        with pytest.raises(File.DoesNotExist):
            file.refresh_from_db()


@pytest.mark.parametrize(
    "config_key",
    [
        "openai_api_key",
    ],
)
def test_openai_ve_voice_provider_error(config_key):
    """Test that any missing param causes failure"""
    form = VoiceProviderType.openai_voice_engine.form_cls(
        team=None,
        data={
            "openai_api_key": "test_key",
            "openai_api_base": "https://openai.com",
            "openai_organization": "test_organization",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.openai_voice_engine, data=form.cleaned_data)


def test_elevenlabs_voice_provider(team_with_users):
    _test_voice_provider(
        team_with_users,
        VoiceProviderType.elevenlabs,
        data={
            "elevenlabs_api_key": "test_key",
            "elevenlabs_model": "eleven_multilingual_v2",
        },
    )


@pytest.mark.django_db()
def test_elevenlabs_synthesize_voice(team_with_users):
    """_synthesize_voice should call SDK with correct params and return SynthesizedAudio"""

    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="voice_id_123",
        neural=True,
        language="English",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )

    fake_mp3_bytes = b"\xff\xfb\x90\x00" * 100

    speech_service = provider.get_speech_service()
    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.text_to_speech.convert.return_value = iter([fake_mp3_bytes])
        with mock.patch("pydub.AudioSegment") as mock_audio:
            mock_segment = mock.Mock()
            mock_segment.__len__ = mock.Mock(return_value=2500)
            mock_audio.from_file.return_value = mock_segment

            result = speech_service._synthesize_voice("Hello world", voice)

    mock_client.text_to_speech.convert.assert_called_once_with(
        voice_id="voice_id_123",
        model_id="eleven_multilingual_v2",
        text="Hello world",
        output_format="mp3_44100_128",
    )
    assert result.format == "mp3"
    assert result.duration == 2.5
    assert isinstance(result.audio, BytesIO)


@pytest.mark.django_db()
def test_elevenlabs_transcribe_audio(team_with_users):
    """_transcribe_audio should call SDK and return transcript text"""

    provider = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )

    speech_service = provider.get_speech_service()
    mock_audio = BytesIO(b"fake audio data")

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_result = mock.Mock()
        mock_result.text = "Hello world"
        mock_client.speech_to_text.convert.return_value = mock_result

        transcript = speech_service._transcribe_audio(mock_audio)

    assert transcript == "Hello world"
    mock_client.speech_to_text.convert.assert_called_once_with(
        file=mock_audio,
        model_id="scribe_v2",
    )


@pytest.mark.parametrize("config_key", ["elevenlabs_api_key"])
def test_elevenlabs_voice_provider_error(config_key):
    """Test that missing API key causes failure"""
    form = VoiceProviderType.elevenlabs.form_cls(
        team=None,
        data={
            "elevenlabs_api_key": "test_key",
            "elevenlabs_model": "eleven_multilingual_v2",
        },
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.elevenlabs, data=form.cleaned_data)


@pytest.mark.django_db()
def test_elevenlabs_sync_voices(team_with_users):
    """sync_voices should create SyntheticVoice records from ElevenLabs API response"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )

    mock_voice_1 = mock.Mock()
    mock_voice_1.voice_id = "voice_id_1"
    mock_voice_1.name = "Rachel"
    mock_voice_1.labels = {"language": "en", "gender": "female"}

    mock_voice_2 = mock.Mock()
    mock_voice_2.voice_id = "voice_id_2"
    mock_voice_2.name = "George"
    mock_voice_2.labels = {"language": "en", "gender": "male"}

    mock_response = mock.Mock()
    mock_response.voices = [mock_voice_1, mock_voice_2]
    mock_response.has_more = False

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.search.return_value = mock_response
        provider.sync_voices()

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 2
    assert voices.filter(name="Rachel", external_id="voice_id_1", gender="female").exists()
    assert voices.filter(name="George", external_id="voice_id_2", gender="male").exists()


@pytest.mark.django_db()
def test_elevenlabs_sync_voices_pagination(team_with_users):
    """sync_voices should follow pagination and collect voices from all pages"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )

    mock_voice_page1 = mock.Mock()
    mock_voice_page1.voice_id = "voice_page1"
    mock_voice_page1.name = "Page1Voice"
    mock_voice_page1.labels = {"language": "en", "gender": "female"}

    mock_voice_page2 = mock.Mock()
    mock_voice_page2.voice_id = "voice_page2"
    mock_voice_page2.name = "Page2Voice"
    mock_voice_page2.labels = {"language": "en", "gender": "male"}

    page1_response = mock.Mock()
    page1_response.voices = [mock_voice_page1]
    page1_response.has_more = True
    page1_response.next_page_token = "token_page2"

    page2_response = mock.Mock()
    page2_response.voices = [mock_voice_page2]
    page2_response.has_more = False

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.search.side_effect = [page1_response, page2_response]
        provider.sync_voices()

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 2
    assert voices.filter(name="Page1Voice", external_id="voice_page1").exists()
    assert voices.filter(name="Page2Voice", external_id="voice_page2").exists()


@pytest.mark.django_db()
def test_elevenlabs_sync_voices_updates_and_removes(team_with_users):
    """sync_voices should update existing voices and remove stale ones not in use"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    # Pre-existing voice that will be updated
    SyntheticVoice.objects.create(
        name="Old Name",
        external_id="voice_id_1",
        neural=True,
        language="en",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    # Pre-existing voice that will be removed (not in API response)
    stale = SyntheticVoice.objects.create(
        name="Stale Voice",
        external_id="voice_id_stale",
        neural=True,
        language="en",
        language_code="en",
        gender="male",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )

    mock_voice = mock.Mock()
    mock_voice.voice_id = "voice_id_1"
    mock_voice.name = "New Name"
    mock_voice.labels = {"language": "en", "gender": "female"}

    mock_response = mock.Mock()
    mock_response.voices = [mock_voice]
    mock_response.has_more = False

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.search.return_value = mock_response
        provider.sync_voices()

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 1
    assert voices.first().name == "New Name"
    assert not SyntheticVoice.objects.filter(pk=stale.pk).exists()


@pytest.mark.parametrize(
    ("labels", "expected_gender"),
    [
        ({"gender": "male"}, "male"),
        ({"gender": "Female"}, "female"),
        ({"gender": "non-binary"}, ""),
        ({}, ""),
        (None, ""),
    ],
)
def test_elevenlabs_gender_mapping(labels, expected_gender):
    assert _map_elevenlabs_gender(labels) == expected_gender


@pytest.mark.django_db()
def test_elevenlabs_add_files_ivc(team_with_users):
    """add_files should upload to ElevenLabs API and create SyntheticVoice with external_id"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    files = FileFactory.create_batch(2, name=factory.Sequence(lambda n: f"voice_{n}.mp3"))

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client_cls.return_value.voices.ivc.create.side_effect = [
            mock.Mock(voice_id="cloned_id_1"),
            mock.Mock(voice_id="cloned_id_2"),
        ]
        provider.add_files(files)

    voices = provider.syntheticvoice_set.all()
    assert len(voices) == 2
    assert voices.filter(external_id="cloned_id_1").exists()
    assert voices.filter(external_id="cloned_id_2").exists()
    for voice in voices:
        assert voice.service == SyntheticVoice.ElevenLabs
        assert voice.file is not None


@pytest.mark.django_db()
def test_elevenlabs_provider_delete(team_with_users):
    """Deleting ElevenLabs provider should attempt API cleanup for cloned voices and delete local records"""
    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    # A synced catalog voice (no file)
    catalog_voice = SyntheticVoice.objects.create(
        name="Rachel",
        external_id="voice_id_1",
        neural=True,
        language="en",
        language_code="en",
        gender="female",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
    )
    # A cloned voice (has file)
    cloned_file = FileFactory.create(name="clone.mp3")
    cloned_voice = SyntheticVoice.objects.create(
        name="My Clone",
        external_id="cloned_id_1",
        neural=True,
        language="",
        language_code="",
        gender="",
        service=SyntheticVoice.ElevenLabs,
        voice_provider=provider,
        file=cloned_file,
    )

    with mock.patch("elevenlabs.client.ElevenLabs") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        provider.delete()
        # Should only attempt to delete the cloned voice from API
        mock_client.voices.delete.assert_called_once_with(voice_id="cloned_id_1")

    # Both local records should be gone
    assert not SyntheticVoice.objects.filter(pk=catalog_voice.pk).exists()
    assert not SyntheticVoice.objects.filter(pk=cloned_voice.pk).exists()

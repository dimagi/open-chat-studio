from unittest import mock

import factory
import pytest

from apps.experiments.models import SyntheticVoice
from apps.files.models import File
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import VoiceProvider, VoiceProviderType
from apps.service_providers.speech_service import SynthesizedAudio
from apps.utils.factories.files import FileFactory


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
    }[provider_type]
    voice = SyntheticVoice(
        name="test", neural=True, language="English", language_code="en", gender="female", service=service
    )

    speech_service = provider.get_speech_service()
    # bypass pydantic validation
    mock_synthesize = mock.Mock(return_value=(SynthesizedAudio(audio=None, duration=0.0, format="mp3")))  # ty: ignore[invalid-argument-type]
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

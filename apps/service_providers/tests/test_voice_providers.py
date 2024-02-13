from unittest import mock

import pytest

from apps.experiments.models import SyntheticVoice
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import VoiceProvider, VoiceProviderType


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
        data={
            "aws_access_key_id": "test_key",
            "aws_secret_access_key": "test_secret",
            "aws_region": "test_region",
        }
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
        data={
            "azure_subscription_key": "test_key",
            "azure_region": "test_region",
        }
    )
    assert form.is_valid()
    form.cleaned_data.pop(config_key)
    _test_voice_provider_error(VoiceProviderType.azure, data=form.cleaned_data)


def _test_voice_provider_error(provider_type: VoiceProviderType, data):
    form = provider_type.form_cls(data=data)
    assert not form.is_valid()

    with pytest.raises(ServiceProviderConfigError):
        provider_type.get_speech_service(data)


def _test_voice_provider(team, provider_type: VoiceProviderType, data):
    form = provider_type.form_cls(data=data)
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
    }[provider_type]
    voice = SyntheticVoice(
        name="test", neural=True, language="English", language_code="en", gender="female", service=service
    )

    speech_service = provider.get_speech_service()
    # bypass pydantic validation
    mock_synthesize = mock.Mock(return_value=(None, 0.0))
    object.__setattr__(speech_service, "_synthesize_voice", mock_synthesize)
    speech_service.synthesize_voice("test", voice)
    assert mock_synthesize.call_count == 1

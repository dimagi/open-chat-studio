import os

import environ
import pytest
from django.conf import settings
from pydub import AudioSegment

from apps.service_providers.speech_service import (
    AWSSpeechService,
    AzureSpeechService,
    OpenAISpeechService,
)
from apps.utils.factories.experiment import SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import VoiceProviderFactory

pytestmark = pytest.mark.speech_integration

# Load environment variables using django-environ
env = environ.Env()

# Try to load .env.integration if it exists, otherwise use regular .env
integration_env = os.path.join(settings.BASE_DIR, ".env.integration")
if os.path.exists(integration_env):
    env.read_env(integration_env)
else:
    env.read_env(os.path.join(settings.BASE_DIR, ".env"))


@pytest.fixture()
def openai_credentials():
    """Get real OpenAI credentials from environment using django-environ"""
    api_key = env.str("OPENAI_API_KEY", default=None)
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")
    return {
        "api_key": api_key,
        "api_base": env.str("OPENAI_API_BASE", default=None),
        "organization": env.str("OPENAI_ORGANIZATION", default=None),
    }


@pytest.fixture()
def aws_credentials():
    """Get real AWS credentials from environment using django-environ"""
    access_key = env.str("AWS_ACCESS_KEY_ID", default=None)
    secret_key = env.str("AWS_SECRET_ACCESS_KEY", default=None)
    region = env.str("AWS_REGION", default="us-east-1")

    if not (access_key and secret_key):
        pytest.skip("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY not set")

    return {
        "access_key_id": access_key,
        "secret_access_key": secret_key,
        "region": region,
    }


@pytest.fixture()
def azure_credentials():
    """Get real Azure credentials from environment using django-environ"""
    subscription_key = env.str("AZURE_SPEECH_KEY", default=None)
    region = env.str("AZURE_SPEECH_REGION", default="eastus")

    if not subscription_key:
        pytest.skip("AZURE_SPEECH_KEY not set")

    return {
        "subscription_key": subscription_key,
        "region": region,
    }


@pytest.mark.django_db()
class TestOpenAISpeechIntegration:
    """Integration tests for OpenAI speech service with real API"""

    def test_synthesize_voice_with_real_api(self, openai_credentials, team_with_users):
        """Test synthesis with real OpenAI API"""
        provider = VoiceProviderFactory(team=team_with_users, type="openai")
        voice = SyntheticVoiceFactory(
            service="OpenAI",
            name="alloy",  # Valid OpenAI voice
            voice_provider=provider,
        )

        service = OpenAISpeechService(
            openai_api_key=openai_credentials["api_key"],
            openai_api_base=openai_credentials["api_base"],
            openai_organization=openai_credentials["organization"],
        )

        # Test synthesis
        text = "Hello, this is a test of the OpenAI speech synthesis."
        result = service.synthesize_voice(text, voice)

        # Verify result
        assert result.format == "mp3"
        assert result.duration > 0
        assert len(result.audio.getvalue()) > 0

        # Verify audio is valid MP3
        audio_segment = AudioSegment.from_file(result.audio, format="mp3")
        assert len(audio_segment) > 0

    def test_transcribe_audio_with_real_api(self, openai_credentials):
        """Test transcription with real OpenAI API - placeholder for future implementation"""
        service = OpenAISpeechService(
            openai_api_key=openai_credentials["api_key"],
            openai_api_base=openai_credentials["api_base"],
            openai_organization=openai_credentials["organization"],
        )

        # TODO: Add real transcription test with valid audio file
        # For now, just verify the service is configured correctly
        assert service.supports_transcription is True


@pytest.mark.django_db()
class TestAWSSpeechIntegration:
    """Integration tests for AWS Polly with real API"""

    def test_synthesize_voice_with_real_api(self, aws_credentials, team_with_users):
        """Test synthesis with real AWS Polly API"""
        provider = VoiceProviderFactory(team=team_with_users, type="aws")
        voice = SyntheticVoiceFactory(
            service="AWS",
            name="Joanna",  # Valid AWS Polly voice
            neural=True,
            voice_provider=provider,
        )

        service = AWSSpeechService(
            aws_access_key_id=aws_credentials["access_key_id"],
            aws_secret_access_key=aws_credentials["secret_access_key"],
            aws_region=aws_credentials["region"],
        )

        text = "Hello from AWS Polly integration test."
        result = service.synthesize_voice(text, voice)

        # Verify result
        assert result.format == "mp3"
        assert result.duration > 0
        assert len(result.audio.getvalue()) > 0


@pytest.mark.django_db()
class TestAzureSpeechIntegration:
    """Integration tests for Azure Cognitive Services with real API"""

    def test_synthesize_voice_with_real_api(self, azure_credentials, team_with_users):
        """Test synthesis with real Azure API"""
        provider = VoiceProviderFactory(team=team_with_users, type="azure")
        voice = SyntheticVoiceFactory(
            service="Azure",
            name="JennyNeural",  # Valid Azure voice
            language_code="en-US",
            voice_provider=provider,
        )

        service = AzureSpeechService(
            azure_subscription_key=azure_credentials["subscription_key"],
            azure_region=azure_credentials["region"],
        )

        text = "Hello from Azure integration test."
        result = service.synthesize_voice(text, voice)

        # Verify result
        assert result.format == "wav"  # Azure returns WAV
        assert result.duration > 0
        assert len(result.audio.getvalue()) > 0

    def test_transcribe_audio_with_real_api(self, azure_credentials):
        """Test transcription with real Azure API - placeholder for future implementation"""
        service = AzureSpeechService(
            azure_subscription_key=azure_credentials["subscription_key"],
            azure_region=azure_credentials["region"],
        )

        # TODO: Add real transcription test with valid audio file
        # For now, just verify the service is configured correctly
        assert service.supports_transcription is True

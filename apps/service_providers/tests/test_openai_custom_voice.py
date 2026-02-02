"""
Tests for OpenAI Custom Voice API client and speech service.
"""

from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.chat.exceptions import AudioSynthesizeException
from apps.experiments.models import SyntheticVoice
from apps.service_providers.openai_custom_voice import (
    CustomVoice,
    OpenAICustomVoiceClient,
    VoiceConsent,
)
from apps.service_providers.speech_service import OpenAICustomVoiceSpeechService


class TestVoiceConsent:
    def test_from_api_response(self):
        data = {
            "id": "cons_123",
            "name": "Test Consent",
            "language": "en-US",
            "created_at": 1234567890,
        }
        consent = VoiceConsent.from_api_response(data)

        assert consent.id == "cons_123"
        assert consent.name == "Test Consent"
        assert consent.language == "en-US"
        assert consent.created_at == 1234567890


class TestCustomVoice:
    def test_from_api_response(self):
        data = {
            "id": "voice_abc",
            "name": "Test Voice",
            "created_at": 1234567890,
        }
        voice = CustomVoice.from_api_response(data)

        assert voice.id == "voice_abc"
        assert voice.name == "Test Voice"
        assert voice.created_at == 1234567890


class TestOpenAICustomVoiceClient:
    @pytest.fixture()
    def client(self):
        return OpenAICustomVoiceClient(api_key="sk-test-123", organization="org-456")

    @pytest.fixture()
    def mock_audio_file(self):
        return BytesIO(b"fake audio data")

    def test_get_consent_phrase_english(self):
        phrase = OpenAICustomVoiceClient.get_consent_phrase("en")
        assert "I am the owner of this voice" in phrase

    def test_get_consent_phrase_unsupported_language(self):
        with pytest.raises(ValueError, match="not available for language"):
            OpenAICustomVoiceClient.get_consent_phrase("xx")

    def test_get_supported_languages(self):
        languages = OpenAICustomVoiceClient.get_supported_languages()
        assert len(languages) == 4
        assert ("en", "English") in languages
        assert ("es", "Spanish") in languages

    def test_get_mime_type(self):
        assert OpenAICustomVoiceClient._get_mime_type("file.mp3") == "audio/mpeg"
        assert OpenAICustomVoiceClient._get_mime_type("file.wav") == "audio/x-wav"
        assert OpenAICustomVoiceClient._get_mime_type("file.ogg") == "audio/ogg"
        assert OpenAICustomVoiceClient._get_mime_type("file.unknown") == "application/octet-stream"

    @patch("httpx.post")
    def test_create_voice_consent_success(self, mock_post, client, mock_audio_file):
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "cons_123",
            "name": "Test Consent",
            "language": "en-US",
            "created_at": 1234567890,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        consent = client.create_voice_consent(
            name="Test Consent",
            language="en-US",
            recording_file=mock_audio_file,
            filename="consent.wav",
        )

        assert consent.id == "cons_123"
        assert consent.name == "Test Consent"
        assert mock_post.called

    @patch("httpx.post")
    def test_create_voice_success(self, mock_post, client, mock_audio_file):
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "voice_abc",
            "name": "Test Voice",
            "created_at": 1234567890,
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        voice = client.create_voice(
            name="Test Voice",
            consent_id="cons_123",
            audio_sample_file=mock_audio_file,
            filename="sample.mp3",
        )

        assert voice.id == "voice_abc"
        assert voice.name == "Test Voice"

    @patch("httpx.get")
    def test_list_voice_consents(self, mock_get, client):
        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "cons_1", "name": "Consent 1", "language": "en-US", "created_at": 123},
                {"id": "cons_2", "name": "Consent 2", "language": "es-ES", "created_at": 456},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        consents = client.list_voice_consents()

        assert len(consents) == 2
        assert consents[0].id == "cons_1"
        assert consents[1].name == "Consent 2"

    @patch("httpx.get")
    def test_list_voices(self, mock_get, client):
        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {"id": "voice_1", "name": "Voice 1", "created_at": 123},
                {"id": "voice_2", "name": "Voice 2", "created_at": 456},
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        voices = client.list_voices()

        assert len(voices) == 2
        assert voices[0].id == "voice_1"

    @patch("httpx.delete")
    def test_delete_voice(self, mock_delete, client):
        mock_response = Mock()
        mock_response.json.return_value = {"deleted": True}
        mock_response.raise_for_status = Mock()
        mock_delete.return_value = mock_response

        result = client.delete_voice("voice_abc")

        assert result is True


class TestOpenAICustomVoiceSpeechService:
    @pytest.fixture()
    def service(self):
        return OpenAICustomVoiceSpeechService(
            openai_api_key="sk-test-123",
            openai_organization="org-456",
        )

    @pytest.fixture()
    def synthetic_voice(self):
        voice = Mock(spec=SyntheticVoice)
        voice.name = "Test Voice"
        voice.service = SyntheticVoice.OpenAICustomVoice
        voice.config = {
            "voice_id": "voice_abc123",
            "consent_id": "cons_def456",
            "model": "gpt-4o-mini-tts",
        }
        return voice

    def test_synthesize_voice_missing_voice_id(self, service):
        """Test that synthesis fails when voice_id is missing"""
        bad_voice = Mock(spec=SyntheticVoice)
        bad_voice.name = "Bad Voice"
        bad_voice.service = SyntheticVoice.OpenAICustomVoice
        bad_voice.config = None

        with pytest.raises(AudioSynthesizeException) as exc_info:
            service._synthesize_voice("Test", bad_voice)

        assert "missing voice_id" in str(exc_info.value)

    def test_synthesize_voice_empty_config(self, service):
        """Test that synthesis fails when config is empty dict"""
        bad_voice = Mock(spec=SyntheticVoice)
        bad_voice.name = "Bad Voice"
        bad_voice.service = SyntheticVoice.OpenAICustomVoice
        bad_voice.config = {}

        with pytest.raises(AudioSynthesizeException) as exc_info:
            service._synthesize_voice("Test", bad_voice)

        assert "missing voice_id" in str(exc_info.value)

    @patch("openai.OpenAI")
    @patch("pydub.AudioSegment")
    def test_synthesize_voice_success(self, mock_audio_segment, mock_openai, service, synthetic_voice):
        """Test successful voice synthesis"""
        # Mock OpenAI client
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock speech response
        mock_response = Mock()
        mock_response.read.return_value = b"fake audio data"
        mock_client.audio.speech.create.return_value = mock_response

        # Mock AudioSegment for duration calculation
        mock_segment = Mock()
        mock_segment.__len__ = Mock(return_value=5000)  # 5 seconds
        mock_audio_segment.from_file.return_value = mock_segment

        # Call service
        result = service._synthesize_voice("Hello world", synthetic_voice)

        # Assertions
        assert result.format == "mp3"
        assert result.duration == 5.0
        mock_client.audio.speech.create.assert_called_once()

        # Verify voice parameter format
        call_kwargs = mock_client.audio.speech.create.call_args[1]
        assert call_kwargs["voice"] == {"id": "voice_abc123"}
        assert call_kwargs["input"] == "Hello world"
        assert call_kwargs["model"] == "gpt-4o-mini-tts"

    @patch("openai.OpenAI")
    @patch("pydub.AudioSegment")
    def test_synthesize_voice_with_instructions(self, mock_audio_segment, mock_openai, service):
        """Test voice synthesis with instructions parameter"""
        # Create voice with instructions
        voice_with_instructions = Mock(spec=SyntheticVoice)
        voice_with_instructions.name = "Test Voice"
        voice_with_instructions.service = SyntheticVoice.OpenAICustomVoice
        voice_with_instructions.config = {
            "voice_id": "voice_abc123",
            "model": "gpt-4o-mini-tts",
            "instructions": "Speak warmly and clearly",
        }

        # Mock OpenAI client
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        mock_response = Mock()
        mock_response.read.return_value = b"fake audio data"
        mock_client.audio.speech.create.return_value = mock_response

        mock_segment = Mock()
        mock_segment.__len__ = Mock(return_value=3000)
        mock_audio_segment.from_file.return_value = mock_segment

        # Call service
        service._synthesize_voice("Hello", voice_with_instructions)

        # Verify instructions were passed
        call_kwargs = mock_client.audio.speech.create.call_args[1]
        assert call_kwargs["instructions"] == "Speak warmly and clearly"

    @patch("openai.OpenAI")
    def test_transcribe_audio(self, mock_openai, service):
        """Test audio transcription"""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock transcription response
        mock_transcript = Mock()
        mock_transcript.text = "Transcribed text"
        mock_client.audio.transcriptions.create.return_value = mock_transcript

        audio_file = BytesIO(b"audio data")
        result = service._transcribe_audio(audio_file)

        assert result == "Transcribed text"
        mock_client.audio.transcriptions.create.assert_called_once()


class TestVoiceProviderCustomVoiceClient:
    """Tests for VoiceProvider.get_custom_voice_client method"""

    def test_get_custom_voice_client_success(self):
        """Test getting custom voice client from provider"""
        from apps.service_providers.models import VoiceProvider, VoiceProviderType

        provider = Mock(spec=VoiceProvider)
        provider.type = VoiceProviderType.openai_custom_voice
        provider.config = {
            "openai_api_key": "sk-test-key",
            "openai_organization": "org-test",
            "openai_api_base": None,
        }

        # Call the actual method
        client = VoiceProvider.get_custom_voice_client(provider)

        assert client is not None
        assert client.api_key == "sk-test-key"
        assert client.organization == "org-test"

    def test_get_custom_voice_client_wrong_provider_type(self):
        """Test that getting client fails for non-custom-voice provider"""
        from apps.service_providers.models import VoiceProvider, VoiceProviderType

        provider = Mock(spec=VoiceProvider)
        provider.type = VoiceProviderType.openai

        with pytest.raises(ValueError, match="Custom voice client not available"):
            VoiceProvider.get_custom_voice_client(provider)

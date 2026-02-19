"""
OpenAI Custom Voice API client for managing voice consents and custom voices.

This module provides a client for OpenAI's stable Audio API with custom voice support,
replacing the deprecated Voice Engine API (/v1/audio/synthesize).
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("ocs.openai_custom_voice")


@dataclass
class VoiceConsent:
    """Represents an OpenAI voice consent recording"""

    id: str
    name: str
    language: str
    created_at: int

    @classmethod
    def from_api_response(cls, data: dict) -> "VoiceConsent":
        return cls(
            id=data["id"],
            name=data["name"],
            language=data["language"],
            created_at=data["created_at"],
        )


@dataclass
class CustomVoice:
    """Represents an OpenAI custom voice"""

    id: str
    name: str
    created_at: int

    @classmethod
    def from_api_response(cls, data: dict) -> "CustomVoice":
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=data["created_at"],
        )


class OpenAICustomVoiceClient:
    """
    Client for OpenAI Custom Voice API operations.
    Handles consent recording uploads and custom voice creation.
    """

    CONSENT_PHRASES = {
        "en": (
            "I am the owner of this voice and I consent to OpenAI using this voice to create a synthetic voice model."
        ),
        "es": (
            "Soy el propietario de esta voz y doy mi consentimiento para que OpenAI "
            "la utilice para crear un modelo de voz sintética."
        ),
        "de": (
            "Ich bin der Eigentümer dieser Stimme und bin damit einverstanden, dass OpenAI "
            "diese Stimme zur Erstellung eines synthetischen Stimmmodells verwendet."
        ),
        "fr": (
            "Je suis le propriétaire de cette voix et j'autorise OpenAI à utiliser cette voix "
            "pour créer un modèle de voix synthétique."
        ),
    }

    def __init__(self, api_key: str, organization: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.organization = organization
        self.base_url = base_url or "https://api.openai.com/v1"

    def _get_headers(self) -> dict:
        """Build request headers"""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        return headers

    # ==================== Voice Consent Operations ====================

    def create_voice_consent(
        self,
        name: str,
        language: str,
        recording_file,
        filename: str,
    ) -> VoiceConsent:
        """
        Upload a consent recording to OpenAI.

        Args:
            name: Label for this consent (e.g., "John Doe")
            language: BCP 47 language tag (e.g., "en-US")
            recording_file: File-like object containing audio
            filename: Original filename (for MIME type detection)

        Returns:
            VoiceConsent object with consent_id

        Raises:
            httpx.HTTPStatusError: If API request fails
        """
        url = f"{self.base_url}/audio/voice_consents"

        files = {"recording": (filename, recording_file, self._get_mime_type(filename))}
        data = {"name": name, "language": language}

        response = httpx.post(url, headers=self._get_headers(), files=files, data=data, timeout=60.0)
        response.raise_for_status()

        return VoiceConsent.from_api_response(response.json())

    def list_voice_consents(self, limit: int = 20, after: str | None = None) -> list[VoiceConsent]:
        """
        List available voice consents.

        Args:
            limit: Number of results (1-100, default 20)
            after: Pagination cursor

        Returns:
            List of VoiceConsent objects
        """
        url = f"{self.base_url}/audio/voice_consents"
        params = {"limit": limit}
        if after:
            params["after"] = after

        response = httpx.get(url, headers=self._get_headers(), params=params, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        return [VoiceConsent.from_api_response(item) for item in data.get("data", [])]

    def get_voice_consent(self, consent_id: str) -> VoiceConsent:
        """Retrieve a specific consent recording"""
        url = f"{self.base_url}/audio/voice_consents/{consent_id}"
        response = httpx.get(url, headers=self._get_headers(), timeout=30.0)
        response.raise_for_status()
        return VoiceConsent.from_api_response(response.json())

    def delete_voice_consent(self, consent_id: str) -> bool:
        """
        Delete a consent recording.

        Warning: This will fail if any voices still reference this consent.
        """
        url = f"{self.base_url}/audio/voice_consents/{consent_id}"
        response = httpx.delete(url, headers=self._get_headers(), timeout=30.0)
        response.raise_for_status()
        return response.json().get("deleted", False)

    # ==================== Custom Voice Operations ====================

    def create_voice(
        self,
        name: str,
        consent_id: str,
        audio_sample_file,
        filename: str,
    ) -> CustomVoice:
        """
        Create a custom voice from audio sample and consent.

        Args:
            name: Display name for the voice
            consent_id: ID from create_voice_consent() (e.g., "cons_1234")
            audio_sample_file: Audio sample file (≤30 seconds)
            filename: Original filename

        Returns:
            CustomVoice object with voice_id

        Raises:
            httpx.HTTPStatusError: If API request fails
            ValueError: If audio sample > 30s or consent invalid
        """
        url = f"{self.base_url}/audio/voices"

        files = {"audio_sample": (filename, audio_sample_file, self._get_mime_type(filename))}
        data = {"name": name, "consent": consent_id}

        response = httpx.post(url, headers=self._get_headers(), files=files, data=data, timeout=60.0)
        response.raise_for_status()

        return CustomVoice.from_api_response(response.json())

    def list_voices(self, limit: int = 20, after: str | None = None) -> list[CustomVoice]:
        """List custom voices available to the organization"""
        url = f"{self.base_url}/audio/voices"
        params = {"limit": limit}
        if after:
            params["after"] = after

        response = httpx.get(url, headers=self._get_headers(), params=params, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        return [CustomVoice.from_api_response(item) for item in data.get("data", [])]

    def get_voice(self, voice_id: str) -> CustomVoice:
        """Retrieve a specific custom voice"""
        url = f"{self.base_url}/audio/voices/{voice_id}"
        response = httpx.get(url, headers=self._get_headers(), timeout=30.0)
        response.raise_for_status()
        return CustomVoice.from_api_response(response.json())

    def delete_voice(self, voice_id: str) -> bool:
        """Delete a custom voice"""
        url = f"{self.base_url}/audio/voices/{voice_id}"
        response = httpx.delete(url, headers=self._get_headers(), timeout=30.0)
        response.raise_for_status()
        return response.json().get("deleted", False)

    # ==================== Helper Methods ====================

    @staticmethod
    def _get_mime_type(filename: str) -> str:
        """Determine MIME type from filename extension"""
        ext = filename.lower().rsplit(".", maxsplit=1)[-1]
        mime_types = {
            "mp3": "audio/mpeg",
            "mpeg": "audio/mpeg",
            "wav": "audio/x-wav",
            "ogg": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "webm": "audio/webm",
            "mp4": "audio/mp4",
        }
        return mime_types.get(ext, "application/octet-stream")

    @classmethod
    def get_consent_phrase(cls, language_code: str) -> str:
        """
        Get the required consent phrase for a language.

        Args:
            language_code: Two-letter language code (e.g., "en", "es")

        Returns:
            Consent phrase string

        Raises:
            ValueError: If language not supported
        """
        phrase = cls.CONSENT_PHRASES.get(language_code)
        if not phrase:
            raise ValueError(f"Consent phrase not available for language: {language_code}")
        return phrase

    @classmethod
    def get_supported_languages(cls) -> list[tuple[str, str]]:
        """
        Get list of supported languages for consent phrases.

        Returns:
            List of (code, name) tuples
        """
        language_names = {
            "en": "English",
            "es": "Spanish",
            "de": "German",
            "fr": "French",
        }
        return [(code, language_names.get(code, code)) for code in cls.CONSENT_PHRASES]

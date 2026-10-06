from enum import StrEnum


class ChatException(Exception):
    def __init__(self, message=""):
        self.message = message
        super().__init__(self.message)


class AudioSynthesizeException(ChatException):
    pass


class AudioTranscriptionException(ChatException):
    pass


class NoSpeechReason(StrEnum):
    """Why a transcriber produced no words."""

    SILENCE = "silence"
    NOT_UNDERSTOOD = "not_understood"


class NoSpeechDetected(AudioTranscriptionException):
    """The transcriber found no speech in the audio: a silent or unintelligible voice note.

    Raised only by the speech service. Nothing failed, so the channel framework catches it
    at QueryExtractionStage and hands the participant a UserActionableError instead -- each
    reason needs participant-facing wording in NO_SPEECH_MESSAGES there.
    """

    def __init__(self, reason: NoSpeechReason):
        self.reason = reason
        super().__init__(reason.value)


class ChannelException(ChatException):
    pass


class ParticipantNotAllowedException(ChatException):
    pass


class VersionedExperimentSessionsNotAllowedException(ChatException):
    pass


class UserActionableError(ChatException):
    """Raised when the participant can fix what went wrong by changing what they send.

    The pipeline answers the participant with a message generated from this error and
    does not re-raise it, so it never fails the task or marks the trace as errored.
    Anything the participant cannot act on -- a provider outage, a revoked key, a bug --
    must use a different exception: see ProviderConfigurationError for the provider
    failures a team can fix. See ADR-0065.
    """


class ModelRefusedTurnError(UserActionableError):
    """The model declined the turn or the provider filtered it; the participant can send something else."""

    MESSAGES = {
        "refusal": "The assistant declined to answer the last message.",
        "content_filter": "The last message was blocked by the provider's content filter.",
    }

    def __init__(self, kind: str, provider_reason: str = "", detail: dict | None = None):
        super().__init__(self.MESSAGES[kind])
        self.kind = kind
        self.provider_reason = provider_reason
        self.detail = detail or {}
        # Celery result backends rebuild an exception from ``args``, so they must match ``__init__``.
        self.args = (kind, provider_reason, self.detail)

    def __str__(self) -> str:
        return self.message

    @property
    def trace_metadata(self) -> dict:
        return {"model_turn_outcome": self.kind, "provider_reason": self.provider_reason, "detail": self.detail}

    @property
    def message_metadata(self) -> dict:
        return {"kind": self.kind, "provider_reason": self.provider_reason}


class EmptyModelResponseError(Exception):
    """The model returned no text, no tool calls and no stop signal."""

    def __init__(self, provider_reason: str = ""):
        self.provider_reason = provider_reason
        super().__init__(provider_reason)

    def __str__(self) -> str:
        return f"The model returned an empty response (stop reason: {self.provider_reason or 'none'})"


class ProviderConfigurationError(ChatException):
    """Raised when an LLM provider rejects a call for a reason only the team can fix.

    Exhausted credits, a revoked API key, a model the provider has withdrawn, a
    conversation past the model's context window. The participant cannot act on any of
    these and no amount of retrying clears them, so the pipeline answers with the canned
    reply and notifies the team rather than failing the task. Transient provider faults
    (rate limits, overload) are deliberately not this: they keep their native SDK
    exception type so the retry policy still recognises them. See ADR-0067.
    """


class ServiceWindowExpiredException(ChatException):
    """Raised when a message cannot be sent because the messaging platform's
    service window has expired and template messages are not configured or
    the message type cannot be sent via template."""

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

    The pipeline answers the participant instead of notifying the team, so each reason
    needs participant-facing wording in MessageProcessingPipeline.NO_SPEECH_PROMPTS.
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
    must use a different exception. See ADR-0065.
    """


class ServiceWindowExpiredException(ChatException):
    """Raised when a message cannot be sent because the messaging platform's
    service window has expired and template messages are not configured or
    the message type cannot be sent via template."""

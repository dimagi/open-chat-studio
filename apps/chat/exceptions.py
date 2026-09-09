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


class UserReportableError(ChatException):
    """A class of errors that can be reported to the end user (participant)"""


class ServiceWindowExpiredException(ChatException):
    """Raised when a message cannot be sent because the messaging platform's
    service window has expired and template messages are not configured or
    the message type cannot be sent via template."""

class ChatException(Exception):
    def __init__(self, message=""):
        self.message = message
        super().__init__(self.message)


class AudioSynthesizeException(ChatException):
    pass


class AudioTranscriptionException(ChatException):
    pass


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

    pass

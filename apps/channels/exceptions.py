class ExperimentChannelException(Exception):
    pass


class UnsupportedMessageTypeException(ExperimentChannelException):
    def __init__(self, message="Unsupported Message"):
        self.message = message
        super().__init__(message)


class InvalidTelegramChannel(ExperimentChannelException):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

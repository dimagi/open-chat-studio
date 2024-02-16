class ExperimentChannelException(Exception):
    pass


class InvalidTelegramChannel(ExperimentChannelException):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

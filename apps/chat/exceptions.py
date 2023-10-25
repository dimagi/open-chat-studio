class ChatException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class AudioSynthesizeException(ChatException):
    def __init__(self, message):
        super().__init__(message)


class MessageHandlerException(ChatException):
    def __init__(self, message):
        super().__init__(message)

class ViewException(Exception):
    def __init__(self, html_message):
        self.html_message = html_message
        super().__init__(self.html_message)


class ChannelAlreadyUtilizedException(ViewException):
    def __init__(self, html_message):
        super().__init__(html_message)

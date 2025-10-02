class EmbeddedWidgetAuthError(Exception):
    """Base exception for embedded widget authentication errors"""

    def __init__(self, message: str):
        self.message = message

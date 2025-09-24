class EmbeddedWidgetAuthError(Exception):
    """Base exception for embedded widget authentication errors"""

    pass


class MissingOriginError(EmbeddedWidgetAuthError):
    """Raised when Origin or Referer header is missing"""

    pass


class InvalidEmbedKeyError(EmbeddedWidgetAuthError):
    """Raised when embed key is invalid or domain not allowed"""

    pass


class InvalidEmbedConfigError(EmbeddedWidgetAuthError):
    """Raised when neither experiment_id nor session is provided"""

    pass

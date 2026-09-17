from django.core.exceptions import ValidationError


class EvaluationRunException(Exception):
    pass


class HistoryParseException(Exception):
    pass


class InFlightRunsError(ValidationError):
    """Raised when a delete is blocked because related EvaluationRuns are still in progress."""


class NoActiveEvaluatorsError(ValidationError):
    """Raised when a run is requested for a config whose evaluators are all archived or absent."""


class SessionSelectionTooLargeError(ValidationError):
    """Raised when a clone would resolve to more sessions than the dataset's mode can handle."""

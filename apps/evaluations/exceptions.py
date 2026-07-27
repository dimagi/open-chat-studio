from django.core.exceptions import ValidationError


class EvaluationRunException(Exception):
    pass


class HistoryParseException(Exception):
    pass


class InFlightRunsError(ValidationError):
    """Raised when a delete is blocked because related EvaluationRuns are still in progress."""

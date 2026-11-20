"""A provider-neutral way to ask typed questions about a run's state instead of generating text.

A provider that offers decision models returns a `DecisionBackend` from `LlmService.get_decision_backend()`.
The pipeline nodes ask their questions through it and never see the provider's own request format.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar


class DecisionError(Exception):
    """A decision request that failed or returned something unusable."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DecisionAuthenticationError(DecisionError):
    """The provider rejected the credentials. Retrying will not help; they have to be changed."""


@dataclass(frozen=True)
class ChoiceQuestion:
    """Answered with one of `options`' keys. Each value says when its key applies, or is None."""

    options: Mapping[str, str | None]
    instructions: str | None = None


Question = ChoiceQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


Answer = ChoiceAnswer


@dataclass(frozen=True)
class DecisionUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class DecisionResponse:
    model: str
    answers: Mapping[str, Answer]
    usage: DecisionUsage
    # The answers as the provider sent them, which is what the run's trace shows.
    raw_answers: Mapping[str, Any]

    def choice(self, name: str) -> ChoiceAnswer | None:
        """The answer to the choice question `name`, or None when there is no well-formed one."""
        answer = self.answers.get(name)
        return answer if isinstance(answer, ChoiceAnswer) else None


class DecisionBackend(ABC):
    """Asks a provider's decision model several named questions in one request."""

    # The provider type the request's usage is reported and priced under.
    provider_type: ClassVar[str]
    # The name the run's trace shows for the call.
    name: ClassVar[str]

    @abstractmethod
    def ask(self, model: str, input: Any, questions: Mapping[str, Question]) -> DecisionResponse:
        """Ask every question in `questions` about `input`, which is JSON-like data, in one request.

        Raises `DecisionAuthenticationError` when the provider rejects the credentials, and `DecisionError`
        when the request fails in any other way.
        """

    @abstractmethod
    def request_text(self, input: Any, questions: Mapping[str, Question]) -> str:
        """The request as text, for the run's trace."""

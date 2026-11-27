"""A provider-neutral way to ask typed questions about a run's state instead of generating text.

A provider that offers decision models returns a `DecisionBackend` from `LlmService.get_decision_backend()`.
The pipeline nodes ask their questions through it and never see the provider's own request format.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
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


@dataclass(frozen=True)
class ScoreQuestion:
    """Answered with a score on the rubric `levels`, one description per level from lowest (0) to highest."""

    levels: Sequence[str]
    instructions: str | None = None


@dataclass(frozen=True)
class YesNoQuestion:
    """Answered with the probability that the statement in `instructions` is true."""

    instructions: str | None = None
    true_description: str | None = None
    false_description: str | None = None


Question = ChoiceQuestion | ScoreQuestion | YesNoQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class YesNoAnswer:
    probability: float
    # Not every provider reports a confidence for a yes/no answer.
    confidence: float | None = None


Answer = ChoiceAnswer | ScoreAnswer | YesNoAnswer


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
        return self._answer(name, ChoiceAnswer)

    def score(self, name: str) -> ScoreAnswer | None:
        """The answer to the score question `name`, or None when there is no well-formed one."""
        return self._answer(name, ScoreAnswer)

    def yes_no(self, name: str) -> YesNoAnswer | None:
        """The answer to the yes/no question `name`, or None when there is no well-formed one."""
        return self._answer(name, YesNoAnswer)

    def _answer[T](self, name: str, answer_type: type[T]) -> T | None:
        answer = self.answers.get(name)
        return answer if isinstance(answer, answer_type) else None


@dataclass(frozen=True)
class DecisionLimits:
    """What one request to a backend may carry. A None limit is one the provider does not set."""

    max_options: int
    min_levels: int
    max_levels: int
    max_questions: int | None = None
    min_options: int = 1
    # A regular expression every question key must match in full.
    key_pattern: str | None = None
    max_option_length: int | None = None
    # A regular expression every option name must match in full.
    option_pattern: str | None = None
    requires_instructions: bool = False

    def accepts_key(self, key: str) -> bool:
        return self.key_pattern is None or re.fullmatch(self.key_pattern, key) is not None

    def accepts_option(self, name: str) -> bool:
        return self.option_pattern is None or re.fullmatch(self.option_pattern, name) is not None


class DecisionBackend(ABC):
    """Asks a provider's decision model several named questions in one request."""

    # The provider type the request's usage is reported and priced under.
    provider_type: ClassVar[str]
    # The name the run's trace shows for the call.
    name: ClassVar[str]
    limits: ClassVar[DecisionLimits]

    @abstractmethod
    def ask(self, model: str, input: Any, questions: Mapping[str, Question]) -> DecisionResponse:
        """Ask every question in `questions` about `input`, which is JSON-like data, in one request.

        Raises `DecisionAuthenticationError` when the provider rejects the credentials, and `DecisionError`
        when the request fails in any other way.
        """

    @abstractmethod
    def request_text(self, input: Any, questions: Mapping[str, Question]) -> str:
        """The request as text, for the run's trace."""

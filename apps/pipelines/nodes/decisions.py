"""The decisions a Decision Node asks a decision model, and the temporary state its answers become."""

import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from apps.pipelines.nodes.mixins import NO_DECISION_BACKEND_MESSAGE
from apps.pipelines.repository import ORMRepository, RepositoryLookupError
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.decision_backend import (
    ChoiceQuestion,
    DecisionLimits,
    DecisionResponse,
    Question,
    ScoreQuestion,
    YesNoQuestion,
)

logger = logging.getLogger("ocs.pipelines.nodes")

# Keys the pipeline itself keeps in the temporary state.
RESERVED_KEYS = frozenset({"user_input", "outputs", "attachments"})

# The params each decision type reads. The rest must be left empty.
TYPE_PARAMS = {
    "choice": {"options", "threshold"},
    "score": {"levels", "threshold"},
    "noul": {"true_description", "false_description"},
}
ALL_TYPE_PARAMS = set().union(*TYPE_PARAMS.values())

Answer = tuple[Any, float | None]


class ChoiceOption(BaseModel):
    name: str = Field(description="The value written to the temporary state when this option is chosen.")
    description: str = Field(default="", description="When this option applies. Optional.")

    @field_validator("name")
    @classmethod
    def strip_name(cls, name: str) -> str:
        """A Static Router matches the written value exactly, so surrounding spaces would miss its route."""
        return name.strip()


class Decision(BaseModel):
    key: str = Field(
        description=(
            "The temporary state key the answer is written to. Choice and score decisions also write "
            "their confidence to `<key>_confidence`, and a noul decision does when the model's provider "
            "reports one."
        )
    )
    type: Literal["choice", "score", "noul"] = Field(
        default="choice",
        description=(
            "`choice` writes the chosen option's name, `score` writes a number from 0 to the highest level, "
            "and `noul` writes the probability, from 0 to 1, that the statement in `instructions` is true."
        ),
    )
    instructions: str = Field(
        default="",
        description="The question to answer. For a `noul` decision, the statement to judge.",
    )
    options: list[ChoiceOption] = Field(
        default_factory=list,
        description="Choice only. The options to choose between. The model's provider sets how many it takes.",
        json_schema_extra={"items": ChoiceOption.model_json_schema()},
    )
    levels: list[str] = Field(
        default_factory=list,
        description=(
            "Score only. The rubric, one description per level from lowest (0) to highest. The model's provider "
            "sets how many levels it takes."
        ),
    )
    true_description: str = Field(default="", description="Noul only. What a true statement means. Optional.")
    false_description: str = Field(default="", description="Noul only. What a false statement means. Optional.")
    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Choice and score only. When the confidence is below this, null is written instead of the answer, "
            "so a Static Router reading the key takes its default route. Leave empty to always write the answer."
        ),
    )

    @field_validator("key")
    @classmethod
    def check_key(cls, key: str) -> str:
        if not key.isidentifier():
            raise PydanticCustomError(
                "invalid_decision_key",
                "'{key}' is not a valid key: use letters, digits and underscores, not starting with a digit",
                {"key": key},
            )
        if key in RESERVED_KEYS:
            raise PydanticCustomError("invalid_decision_key", "The key '{key}' is reserved", {"key": key})
        return key

    @model_validator(mode="after")
    def check_type_params(self) -> Self:
        stray = [param for param in sorted(ALL_TYPE_PARAMS - TYPE_PARAMS[self.type]) if getattr(self, param)]
        if stray:
            raise decision_error(self, "{param} does not apply to a {type} decision", param=stray[0], type=self.type)
        TYPE_CHECKS[self.type](self)
        return self

    @property
    def confidence_key(self) -> str:
        return f"{self.key}_confidence"

    def question(self) -> Question:
        return QUESTION_BUILDERS[self.type](self)

    def answer(self, response: DecisionResponse) -> Answer:
        """The value and confidence the model gave, each None where it gave none."""
        return ANSWER_READERS[self.type](self, response)

    def temp_state(self, value: Any, confidence: float | None, yes_no_confidence: bool) -> dict[str, Any]:
        """The entries this decision writes: null for the value when the confidence is below the threshold.

        Not every backend reports a confidence for a noul, so a noul writes one only when `yes_no_confidence`
        says its backend does. It then writes null when this answer has none, so that an earlier node's
        confidence under the same key does not outlive it.
        """
        values = {self.key: None if self._below_threshold(confidence) else value}
        if self.type != "noul" or yes_no_confidence:
            values[self.confidence_key] = confidence
        return values

    def _below_threshold(self, confidence: float | None) -> bool:
        if self.threshold is None or confidence is None:
            return False
        return confidence < self.threshold


def decision_error(decision: Decision, message: str, **context: str) -> PydanticCustomError:
    return PydanticCustomError("invalid_decision", message, {"key": decision.key, "field": "decisions", **context})


def _check_options(decision: Decision) -> None:
    names = [_normalized(option.name) for option in decision.options]
    if not names:
        raise decision_error(decision, "Decision {key} needs at least one option")
    if not all(names):
        raise decision_error(decision, "Every option of decision {key} needs a name")
    if len(set(names)) != len(names):
        raise decision_error(decision, "Decision {key} has an option named more than once")


def _check_levels(decision: Decision) -> None:
    if not decision.levels:
        raise decision_error(decision, "Decision {key} needs at least one level")
    if not all(level.strip() for level in decision.levels):
        raise decision_error(decision, "Decision {key} must describe every level")


TYPE_CHECKS: dict[str, Callable[[Decision], None]] = {
    "choice": _check_options,
    "score": _check_levels,
    "noul": lambda decision: None,
}


def check_decision_limits(decisions: list[Decision], limits: DecisionLimits) -> None:
    """Raise when the decisions ask more of one request than the backend's `limits` allow."""
    if limits.max_questions is not None and len(decisions) > limits.max_questions:
        raise PydanticCustomError(
            "invalid_decisions",
            "This model answers at most {max} decisions in one call",
            {"max": limits.max_questions, "field": "decisions"},
        )
    for decision in decisions:
        _check_key_and_instructions(decision, limits)
        LIMIT_CHECKS[decision.type](decision, limits)


def _check_key_and_instructions(decision: Decision, limits: DecisionLimits) -> None:
    if not limits.accepts_key(decision.key):
        message = "'{key}' is not a key this model accepts: keys must match {pattern}"
        raise decision_error(decision, message, pattern=str(limits.key_pattern))
    if limits.requires_instructions and not decision.instructions.strip():
        raise decision_error(decision, "Decision {key} needs instructions for this model")


def _check_option_count(decision: Decision, limits: DecisionLimits) -> None:
    names = [option.name for option in decision.options]
    check_option_limits(names, limits, subject=f"Decision {decision.key}", noun="options", field="decisions")


def check_option_limits(names: Sequence[str], limits: DecisionLimits, subject: str, noun: str, field: str) -> None:
    """Raise when the backend takes fewer or more options than `names`, or none as long as one of them."""
    context = {"subject": subject, "noun": noun, "field": field}
    if not limits.min_options <= len(names) <= limits.max_options:
        bounds = {"min": limits.min_options, "max": limits.max_options}
        raise PydanticCustomError("invalid_decision", "{subject} needs {min} to {max} {noun}", context | bounds)
    longest = limits.max_option_length
    if longest is not None and any(len(name) > longest for name in names):
        message = "{subject} needs {noun} of at most {max} characters"
        raise PydanticCustomError("invalid_decision", message, context | {"max": longest})


def decision_limits(llm_provider_id: int, require_backend: bool = False) -> DecisionLimits | None:
    """The request limits of the provider's decision backend, or None when it has none or cannot be loaded.

    A provider that cannot be loaded is reported when the node runs, so validation does not fail on it first.
    With `require_backend`, a provider that has no decision backend raises instead.
    """
    try:
        backend = ORMRepository().get_llm_service(llm_provider_id).get_decision_backend()
    except (RepositoryLookupError, ServiceProviderConfigError):
        return None
    if backend is None and require_backend:
        raise PydanticCustomError("invalid_provider", NO_DECISION_BACKEND_MESSAGE, {"field": "llm_provider_id"})
    return backend.limits if backend else None


def _check_level_count(decision: Decision, limits: DecisionLimits) -> None:
    if not limits.min_levels <= len(decision.levels) <= limits.max_levels:
        bounds = {"min": str(limits.min_levels), "max": str(limits.max_levels)}
        raise decision_error(decision, "Decision {key} needs {min} to {max} levels", **bounds)


LIMIT_CHECKS: dict[str, Callable[[Decision, DecisionLimits], None]] = {
    "choice": _check_option_count,
    "score": _check_level_count,
    "noul": lambda decision, limits: None,
}


def _normalized(name: str) -> str:
    return name.strip().lower()


def _optional(text: str) -> str | None:
    return text.strip() or None


def _choice_question(decision: Decision) -> Question:
    options = {option.name: _optional(option.description) for option in decision.options}
    return ChoiceQuestion(options=options, instructions=_optional(decision.instructions))


def _score_question(decision: Decision) -> Question:
    return ScoreQuestion(levels=decision.levels, instructions=_optional(decision.instructions))


def _noul_question(decision: Decision) -> Question:
    return YesNoQuestion(
        instructions=_optional(decision.instructions),
        true_description=_optional(decision.true_description),
        false_description=_optional(decision.false_description),
    )


QUESTION_BUILDERS: dict[str, Callable[[Decision], Question]] = {
    "choice": _choice_question,
    "score": _score_question,
    "noul": _noul_question,
}


def _read_choice(decision: Decision, response: DecisionResponse) -> Answer:
    choice = response.choice(decision.key)
    if choice is None:
        return None, None
    return _option_named(decision, choice.choice), choice.confidence


def _read_score(decision: Decision, response: DecisionResponse) -> Answer:
    score = response.score(decision.key)
    if score is None:
        return None, None
    on_the_rubric = 0 <= score.score <= len(decision.levels) - 1
    return (score.score if on_the_rubric else None), score.confidence


def _read_noul(decision: Decision, response: DecisionResponse) -> Answer:
    answer = response.yes_no(decision.key)
    return (answer.probability, answer.confidence) if answer else (None, None)


ANSWER_READERS: dict[str, Callable[[Decision, DecisionResponse], Answer]] = {
    "choice": _read_choice,
    "score": _read_score,
    "noul": _read_noul,
}


def _option_named(decision: Decision, name: str) -> str | None:
    """The configured option the model named, matched without regard to case or surrounding space."""
    wanted = _normalized(name)
    return next((option.name for option in decision.options if _normalized(option.name) == wanted), None)


def check_decision_keys(decisions: list[Decision]) -> list[Decision]:
    """Each decision writes keys no other decision writes."""
    if not decisions:
        raise PydanticCustomError("invalid_decisions", "Add at least one decision")
    _check_unique_keys([decision.key for decision in decisions])
    _check_confidence_keys(decisions)
    return decisions


def _check_unique_keys(keys: list[str]) -> None:
    for index, key in enumerate(keys):
        if key in keys[:index]:
            raise PydanticCustomError("invalid_decisions", "More than one decision writes to '{key}'", {"key": key})


def _check_confidence_keys(decisions: list[Decision]) -> None:
    owners = {decision.confidence_key: decision.key for decision in decisions}
    clash = next((decision.key for decision in decisions if decision.key in owners), None)
    if clash:
        raise PydanticCustomError(
            "invalid_decisions",
            "The key '{key}' is where {owner} writes its confidence",
            {"key": clash, "owner": owners[clash]},
        )


def decision_questions(decisions: list[Decision]) -> dict[str, Question]:
    return {decision.key: decision.question() for decision in decisions}


def temp_state_values(
    node_name: str, decisions: list[Decision], response: DecisionResponse | None, yes_no_confidence: bool
) -> dict[str, Any]:
    """The temporary state each decision writes. A decision with no usable answer writes null.

    `yes_no_confidence` is whether the backend reports a confidence for a noul.
    """
    values = {}
    for decision in decisions:
        value, confidence = decision.answer(response) if response else (None, None)
        if response and value is None:
            logger.warning("Decision node %s got no usable answer for %s", node_name, decision.key)
        values |= decision.temp_state(value, confidence, yes_no_confidence)
    return values

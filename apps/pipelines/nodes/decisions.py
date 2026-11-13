"""The decisions a Decision Node asks Jev, and the temporary state its answers become."""

import logging
from collections.abc import Callable
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from apps.service_providers.llm_service.jev import (
    ChoiceQuestion,
    JevResponse,
    NoulQuestion,
    Question,
    ScoreQuestion,
)

logger = logging.getLogger("ocs.pipelines.nodes")

# Keys the pipeline itself keeps in the temporary state.
RESERVED_KEYS = frozenset({"user_input", "outputs", "attachments"})
MAX_OPTIONS = 255
MAX_LEVELS = 10

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
            "their confidence to `<key>_confidence`."
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
        description="Choice only. The options to choose between, 1 to 255.",
        json_schema_extra={"items": ChoiceOption.model_json_schema()},
    )
    levels: list[str] = Field(
        default_factory=list,
        description="Score only. The rubric, one description per level from lowest (0) to highest, 1 to 10 levels.",
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
    def confidence_key(self) -> str | None:
        return None if self.type == "noul" else f"{self.key}_confidence"

    def question(self) -> Question:
        return QUESTION_BUILDERS[self.type](self)

    def answer(self, response: JevResponse) -> Answer:
        """The value and confidence Jev gave, each None where it gave none."""
        return ANSWER_READERS[self.type](self, response)

    def temp_state(self, value: Any, confidence: float | None) -> dict[str, Any]:
        """The entries this decision writes: null for the value when the confidence is below the threshold."""
        values = {self.key: None if self._below_threshold(confidence) else value}
        if self.confidence_key:
            values[self.confidence_key] = confidence
        return values

    def _below_threshold(self, confidence: float | None) -> bool:
        if self.threshold is None or confidence is None:
            return False
        return confidence < self.threshold


def decision_error(decision: Decision, message: str, **context: str) -> PydanticCustomError:
    return PydanticCustomError("invalid_decision", message, {"key": decision.key, **context})


def _check_options(decision: Decision) -> None:
    names = [_normalized(option.name) for option in decision.options]
    if not 1 <= len(names) <= MAX_OPTIONS:
        raise decision_error(decision, "Decision {key} needs 1 to 255 options")
    if not all(names):
        raise decision_error(decision, "Every option of decision {key} needs a name")
    if len(set(names)) != len(names):
        raise decision_error(decision, "Decision {key} has an option named more than once")


def _check_levels(decision: Decision) -> None:
    if not 1 <= len(decision.levels) <= MAX_LEVELS:
        raise decision_error(decision, "Decision {key} needs 1 to 10 levels")
    if not all(level.strip() for level in decision.levels):
        raise decision_error(decision, "Decision {key} must describe every level")


TYPE_CHECKS: dict[str, Callable[[Decision], None]] = {
    "choice": _check_options,
    "score": _check_levels,
    "noul": lambda decision: None,
}


def _normalized(name: str) -> str:
    return name.strip().lower()


def _optional(text: str) -> str | None:
    return text.strip() or None


def _choice_question(decision: Decision) -> Question:
    criteria = {option.name: _optional(option.description) for option in decision.options}
    return ChoiceQuestion(criteria=criteria, instructions=_optional(decision.instructions))


def _score_question(decision: Decision) -> Question:
    return ScoreQuestion(criteria=decision.levels, instructions=_optional(decision.instructions))


def _noul_question(decision: Decision) -> Question:
    criteria = {"true": _optional(decision.true_description), "false": _optional(decision.false_description)}
    has_criteria = any(criteria.values())
    return NoulQuestion(instructions=_optional(decision.instructions), criteria=criteria if has_criteria else None)


QUESTION_BUILDERS: dict[str, Callable[[Decision], Question]] = {
    "choice": _choice_question,
    "score": _score_question,
    "noul": _noul_question,
}


def _read_choice(decision: Decision, response: JevResponse) -> Answer:
    choice = response.choice(decision.key)
    if choice is None:
        return None, None
    return _option_named(decision, choice.choice), choice.confidence


def _read_score(decision: Decision, response: JevResponse) -> Answer:
    score = response.score(decision.key)
    if score is None:
        return None, None
    return score.score, score.confidence


def _read_noul(decision: Decision, response: JevResponse) -> Answer:
    noul = response.noul(decision.key)
    return (noul.noul if noul else None), None


ANSWER_READERS: dict[str, Callable[[Decision, JevResponse], Answer]] = {
    "choice": _read_choice,
    "score": _read_score,
    "noul": _read_noul,
}


def _option_named(decision: Decision, name: str) -> str | None:
    """The configured option Jev named, matched without regard to case or surrounding space."""
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
    owners = {decision.confidence_key: decision.key for decision in decisions if decision.confidence_key}
    clash = next((decision.key for decision in decisions if decision.key in owners), None)
    if clash:
        raise PydanticCustomError(
            "invalid_decisions",
            "The key '{key}' is where {owner} writes its confidence",
            {"key": clash, "owner": owners[clash]},
        )


def decision_questions(decisions: list[Decision]) -> dict[str, Question]:
    return {decision.key: decision.question() for decision in decisions}


def temp_state_values(node_name: str, decisions: list[Decision], response: JevResponse | None) -> dict[str, Any]:
    """The temporary state each decision writes. A decision with no usable answer writes null."""
    values = {}
    for decision in decisions:
        value, confidence = decision.answer(response) if response else (None, None)
        if response and value is None:
            logger.warning("Decision node %s got no usable answer for %s from Jev", node_name, decision.key)
        values |= decision.temp_state(value, confidence)
    return values

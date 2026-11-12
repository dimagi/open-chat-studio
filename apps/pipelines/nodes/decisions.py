"""The decisions a Decision Node asks Jev, and the temporary state its answers become."""

import logging
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


class ChoiceOption(BaseModel):
    name: str = Field(description="The value written to the temporary state when this option is chosen.")
    description: str = Field(default="", description="When this option applies. Optional.")


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
        for param in sorted(ALL_TYPE_PARAMS - TYPE_PARAMS[self.type]):
            if getattr(self, param):
                raise self._error("{param} does not apply to a {type} decision", param=param, type=self.type)
        if self.type == "choice":
            self._check_options()
        elif self.type == "score":
            self._check_levels()
        return self

    def _check_options(self) -> None:
        if not 1 <= len(self.options) <= MAX_OPTIONS:
            raise self._error("Decision {key} needs 1 to 255 options")
        names = [option.name.strip().lower() for option in self.options]
        if not all(names):
            raise self._error("Every option of decision {key} needs a name")
        if len(set(names)) != len(names):
            raise self._error("Decision {key} has an option named more than once")

    def _check_levels(self) -> None:
        if not 1 <= len(self.levels) <= MAX_LEVELS:
            raise self._error("Decision {key} needs 1 to 10 levels")
        if not all(level.strip() for level in self.levels):
            raise self._error("Decision {key} must describe every level")

    def _error(self, message: str, **context: str) -> PydanticCustomError:
        return PydanticCustomError("invalid_decision", message, {"key": self.key, **context})

    @property
    def confidence_key(self) -> str | None:
        return None if self.type == "noul" else f"{self.key}_confidence"

    def question(self) -> Question:
        instructions = self.instructions.strip() or None
        if self.type == "choice":
            criteria = {option.name: option.description.strip() or None for option in self.options}
            return ChoiceQuestion(criteria=criteria, instructions=instructions)
        if self.type == "score":
            return ScoreQuestion(criteria=self.levels, instructions=instructions)
        return NoulQuestion(instructions=instructions, criteria=self._noul_criteria())

    def _noul_criteria(self) -> dict[str, str | None] | None:
        if not (self.true_description.strip() or self.false_description.strip()):
            return None
        return {"true": self.true_description.strip() or None, "false": self.false_description.strip() or None}

    def answer(self, response: JevResponse) -> tuple[Any, float | None]:
        """The value and confidence Jev gave, each None where it gave none."""
        if self.type == "choice":
            choice = response.choice(self.key)
            return (self._option_named(choice.choice), choice.confidence) if choice else (None, None)
        if self.type == "score":
            score = response.score(self.key)
            return (score.score, score.confidence) if score else (None, None)
        noul = response.noul(self.key)
        return (noul.noul if noul else None), None

    def temp_state(self, value: Any, confidence: float | None) -> dict[str, Any]:
        """The entries this decision writes: null for the value when the confidence is below the threshold."""
        below_threshold = self.threshold is not None and confidence is not None and confidence < self.threshold
        values = {self.key: None if below_threshold else value}
        if self.confidence_key:
            values[self.confidence_key] = confidence
        return values

    def _option_named(self, name: str) -> str | None:
        for option in self.options:
            if option.name.strip().lower() == name.strip().lower():
                return option.name
        return None


def check_decision_keys(decisions: list[Decision]) -> list[Decision]:
    """Each decision writes keys no other decision writes."""
    if not decisions:
        raise PydanticCustomError("invalid_decisions", "Add at least one decision")
    keys = [decision.key for decision in decisions]
    confidence_keys = {decision.confidence_key: decision.key for decision in decisions if decision.confidence_key}
    for index, key in enumerate(keys):
        if key in keys[:index]:
            raise PydanticCustomError("invalid_decisions", "More than one decision writes to '{key}'", {"key": key})
        if key in confidence_keys:
            raise PydanticCustomError(
                "invalid_decisions",
                "The key '{key}' is where {owner} writes its confidence",
                {"key": key, "owner": confidence_keys[key]},
            )
    return decisions


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

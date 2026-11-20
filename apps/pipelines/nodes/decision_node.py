"""The Decision Node: several Jev questions in one call, with the answers written to the temporary state."""

import json
import logging
from typing import TYPE_CHECKING, ClassVar, Self

from django.conf import settings
from pydantic import Field, field_validator, model_validator
from pydantic.config import ConfigDict
from pydantic_core import PydanticCustomError

from apps.pipelines.models import PipelineChatHistoryTypes
from apps.pipelines.nodes.base import ModelUse, NodeSchema, PipelineNode, PipelineState, UiSchema, Widgets
from apps.pipelines.nodes.decision_calls import ask_decisions
from apps.pipelines.nodes.decisions import (
    Decision,
    check_decision_keys,
    check_decision_limits,
    decision_questions,
    temp_state_values,
)
from apps.pipelines.nodes.mixins import HISTORY_TYPE_DESCRIPTION, HistoryMixin, llm_provider_id_field
from apps.pipelines.repository import ORMRepository, RepositoryLookupError
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.decision_backend import DecisionError, DecisionLimits
from apps.service_providers.models import LlmProviderModel

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext

logger = logging.getLogger("ocs.pipelines.nodes")

JEV_MODEL_REQUIRED_MESSAGE = "The Decision Node needs a TypeSafe Jev model"


class DecisionNode(PipelineNode, HistoryMixin):
    """Answers several questions about the conversation in one Jev call and writes the answers to the temporary state"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Decision Node",
            icon="fa-solid fa-list-check",
            documentation_link=settings.DOCUMENTATION_LINKS["node_decision"],
            field_order=["llm_provider_id", "history_type", "decisions"],
        )
    )
    model_use: ClassVar[ModelUse] = ModelUse.decisions

    llm_provider_id: int = llm_provider_id_field(
        ModelUse.decisions,
        title="Model",
        description="The TypeSafe Jev provider this node calls.",
    )
    history_type: PipelineChatHistoryTypes = Field(
        PipelineChatHistoryTypes.GLOBAL,
        description=HISTORY_TYPE_DESCRIPTION,
        json_schema_extra=UiSchema(widget=Widgets.history, enum_labels=PipelineChatHistoryTypes.labels),
    )
    decisions: list[Decision] = Field(
        default_factory=list,
        validate_default=True,
        description=(
            "The questions to answer, all in one call. Each answer is written to the temporary state under its "
            "decision's `key`, and the node passes its input on unchanged."
        ),
        json_schema_extra=UiSchema(widget=Widgets.decisions),
    )

    @field_validator("decisions")
    @classmethod
    def check_keys(cls, value: list[Decision]) -> list[Decision]:
        return check_decision_keys(value)

    @model_validator(mode="after")
    def check_limits(self) -> Self:
        if limits := self._decision_limits():
            check_decision_limits(self.decisions, limits)
        return self

    def _decision_limits(self) -> DecisionLimits | None:
        """The selected provider's request limits, or None when its provider cannot be loaded, which the run reports."""
        try:
            backend = ORMRepository().get_llm_service(self.llm_provider_id).get_decision_backend()
        except (RepositoryLookupError, ServiceProviderConfigError):
            return None
        return backend.limits if backend else None

    def check_model(self, model: LlmProviderModel) -> None:
        if not model.supports_decisions:
            raise PydanticCustomError("invalid_model", JEV_MODEL_REQUIRED_MESSAGE, {"field": "llm_provider_model_id"})

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        backend = self.get_decision_backend()
        try:
            response = ask_decisions(self, context, decision_questions(self.decisions), backend)
        except DecisionError as e:
            logger.warning("Decision node %s got no answers from %s: %s", self.name, backend.name, e)
            response = None

        values = temp_state_values(self.name, self.decisions, response)
        if context.session:
            self.save_history(context.input, json.dumps(values))
        return PipelineState.from_node_output(
            node_name=self.name, node_id=self.node_id, output=context.input, temp_state=values
        )

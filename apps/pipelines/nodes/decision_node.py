"""The Decision Node: several Jev questions in one call, with the answers written to the temporary state."""

import json
import logging
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from pydantic import Field, field_validator
from pydantic.config import ConfigDict
from pydantic_core import PydanticCustomError

from apps.pipelines.exceptions import NodeUserConfigRunError
from apps.pipelines.models import PipelineChatHistoryTypes
from apps.pipelines.nodes.base import NodeSchema, PipelineNode, PipelineState, UiSchema, Widgets
from apps.pipelines.nodes.decisions import Decision, check_decision_keys, decision_questions, temp_state_values
from apps.pipelines.nodes.jev_calls import ask_jev
from apps.pipelines.nodes.mixins import HISTORY_TYPE_DESCRIPTION, HistoryMixin, llm_provider_id_field
from apps.service_providers.llm_service import JevLlmService
from apps.service_providers.llm_service.jev import JevError
from apps.service_providers.models import LlmProviderTypes

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
    accepts_router_only_providers: ClassVar[bool] = True

    llm_provider_id: int = llm_provider_id_field(
        accepts_router_only_providers=True,
        included_provider_types=LlmProviderTypes.router_only_slugs,
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

    def check_provider_type(self, provider_type: str) -> None:
        if provider_type not in LlmProviderTypes.router_only_slugs:
            raise PydanticCustomError("invalid_model", JEV_MODEL_REQUIRED_MESSAGE, {"field": "llm_provider_model_id"})

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        llm_service = self.get_llm_service()
        if not isinstance(llm_service, JevLlmService):
            raise NodeUserConfigRunError(JEV_MODEL_REQUIRED_MESSAGE)
        try:
            response = ask_jev(self, context, decision_questions(self.decisions), llm_service.get_jev_client())
        except JevError as e:
            logger.warning("Decision node %s got no answers from Jev: %s", self.name, e)
            response = None

        values = temp_state_values(self.name, self.decisions, response)
        if context.session:
            self.save_history(context.input, json.dumps(values))
        return PipelineState.from_node_output(
            node_name=self.name, node_id=self.node_id, output=context.input, temp_state=values
        )

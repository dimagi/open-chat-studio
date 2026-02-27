import json
import logging
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import TextChoices
from jinja2.sandbox import SandboxedEnvironment
from langchain.agents import create_agent
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import HumanMessage
from langchain_core.prompts import PromptTemplate
from langchain_openai.chat_models.base import OpenAIRefusalError
from langgraph.constants import END
from langgraph.types import Command, interrupt
from pydantic import BaseModel, BeforeValidator, Field, field_serializer, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic.config import ConfigDict
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import FieldValidationInfo

from apps.annotations.models import TagCategories
from apps.assistants.models import OpenAiAssistant
from apps.experiments.models import BuiltInTools, ExperimentSession
from apps.files.models import FilePurpose
from apps.pipelines.exceptions import (
    AbortPipeline,
    CodeNodeRunError,
    PipelineNodeBuildError,
    PipelineNodeRunError,
    WaitForNextInput,
)
from apps.pipelines.models import (
    PipelineChatHistoryTypes,
)
from apps.pipelines.nodes.base import (
    NodeSchema,
    OptionsSource,
    PipelineNode,
    PipelineRouterNode,
    PipelineState,
    UiSchema,
    VisibleWhen,
    Widgets,
    deprecated_node,
)
from apps.pipelines.nodes.context import PipelineAccessor
from apps.pipelines.nodes.helpers import get_system_message
from apps.pipelines.nodes.llm_node import execute_sub_agent
from apps.pipelines.repository import ORMRepository, RepositoryLookupError
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.llm_service.adapters import AssistantAdapter
from apps.service_providers.llm_service.history_managers import AssistantPipelineHistoryManager
from apps.service_providers.llm_service.prompt_context import (
    PipelineParticipantDataProxy,
    PromptTemplateContext,
)
from apps.service_providers.llm_service.retry import with_llm_retry
from apps.service_providers.llm_service.runnables import (
    AgentAssistantChat,
    AssistantChat,
    ChainOutput,
)
from apps.utils.prompt import PromptVars, validate_prompt_variables
from apps.utils.python_execution import RestrictedPythonExecutionMixin, get_code_error_message
from apps.utils.restricted_http import RestrictedHttpClient

from .mixins import (
    ExtractStructuredDataNodeMixin,
    HistoryMixin,
    LLMResponseMixin,
    OutputMessageTagMixin,
    RouterMixin,
    StructuredDataSchemaValidatorMixin,
)

if TYPE_CHECKING:
    from apps.pipelines.nodes.context import NodeContext

logger = logging.getLogger("ocs.pipelines.nodes")

OptionalInt = Annotated[int | None, BeforeValidator(lambda x: None if isinstance(x, str) and len(x) == 0 else x)]

CODE_NODE_DOCS = f"{settings.DOCUMENTATION_BASE_URL}{settings.DOCUMENTATION_LINKS['node_code']}"
DEFAULT_FUNCTION = f"""# You must define a main function, which takes the node input as a string.
# Return a string to pass to the next node.

# Learn more about Python nodes at {CODE_NODE_DOCS}

def main(input: str, **kwargs) -> str:
    return input
"""


class RenderTemplate(PipelineNode, OutputMessageTagMixin):
    """Renders a Jinja template"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Render a template",
            icon="fa-solid fa-robot",
            documentation_link=settings.DOCUMENTATION_LINKS["node_template"],
        )
    )
    template_string: str = Field(
        description="Use {{your_variable_name}} to refer to designate input",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        env = SandboxedEnvironment()
        try:
            content = {
                "input": context.input,
                "node_inputs": context.inputs,
                "temp_state": context.state.temp,
                "session_state": context.state.session_state,
                "input_message_id": context.input_message_id,
                "input_message_url": context.input_message_url,
            }

            session = context.session
            if session:
                participant = self.repo.get_session_participant(session)
                if participant:
                    content.update(
                        {
                            "participant_details": {
                                "identifier": getattr(participant, "identifier", None),
                                "platform": getattr(participant, "platform", None),
                            },
                            "participant_schedules": self.repo.get_participant_schedules(
                                participant,
                                session.experiment_id,
                                as_dict=True,
                                include_inactive=True,
                            )
                            or [],
                        }
                    )
                content["participant_data"] = context.state.participant_data

            template = env.from_string(self.template_string)
            output = template.render(content)
        except Exception as e:
            raise PipelineNodeRunError(f"Error rendering template: {e}") from e

        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output)


@deprecated_node(message="Use the 'LLM' node instead.")
class LLMResponse(PipelineNode, LLMResponseMixin):
    """Calls an LLM with the given input"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="LLM response"))

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        llm = with_llm_retry(self.get_chat_model())
        output = llm.invoke(context.input, config=self._config)
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output.content)


class ToolConfigModel(BaseModel):
    allowed_domains: list[str] = Field(
        default_factory=list,
        json_schema_extra=UiSchema(
            widget=Widgets.none,
        ),
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        json_schema_extra=UiSchema(
            widget=Widgets.none,
        ),
    )

    @field_validator("allowed_domains", "blocked_domains", mode="after")
    @classmethod
    def validate_domains(cls, value: list[str], info) -> list[str]:
        values = list(map(str.strip, filter(None, value)))
        for domain in values:
            try:
                validators.validate_domain_name(domain)
            except ValidationError:
                raise ValueError(f"Invalid domain name '{domain}' in field '{info.field_name}'") from None
        return values

    @field_serializer("allowed_domains", "blocked_domains")
    def serialize_lists(self, values: list[str]) -> list[str] | None:
        # return None instead of empty list
        return values if values else None


class LLMResponseWithPrompt(LLMResponse, HistoryMixin, OutputMessageTagMixin):
    """Uses an LLM to respond to the input."""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="LLM",
            icon="fa-solid fa-wand-magic-sparkles",
            documentation_link=settings.DOCUMENTATION_LINKS["node_llm"],
        )
    )

    source_material_id: OptionalInt = Field(
        None, json_schema_extra=UiSchema(widget=Widgets.select, options_source=OptionsSource.source_material)
    )
    prompt: str = Field(
        default="You are a helpful assistant. Answer the user's query as best you can",
        json_schema_extra=UiSchema(
            widget=Widgets.text_editor, options_source=OptionsSource.text_editor_autocomplete_vars_llm_node
        ),
    )
    collection_id: OptionalInt = Field(
        None,
        title="Media",
        json_schema_extra=UiSchema(widget=Widgets.select, options_source=OptionsSource.collection),
    )
    collection_index_ids: list[int] = Field(
        default_factory=list,
        title="Collection Indexes",
        json_schema_extra=UiSchema(
            widget=Widgets.searchable_multiselect, options_source=OptionsSource.collection_index
        ),
    )
    max_results: OptionalInt = Field(
        default=20,
        ge=1,
        le=100,
        description="The maximum number of results to retrieve from the index",
        json_schema_extra=UiSchema(
            widget=Widgets.range,
            visible_when=VisibleWhen(field="collection_index_ids", operator="is_not_empty"),
        ),
    )
    generate_citations: bool = Field(
        default=True,
        description="Allow files from this collection to be referenced in LLM responses and downloaded by users",
        json_schema_extra=UiSchema(
            widget=Widgets.toggle,
            visible_when=VisibleWhen(field="collection_index_ids", operator="is_not_empty"),
        ),
    )

    tools: list[str] = Field(
        default_factory=list,
        description="The tools to enable for the bot",
        json_schema_extra=UiSchema(widget=Widgets.multiselect, options_source=OptionsSource.agent_tools),
    )
    custom_actions: list[str] = Field(
        default_factory=list,
        description="Custom actions to enable for the bot",
        json_schema_extra=UiSchema(widget=Widgets.multiselect, options_source=OptionsSource.custom_actions),
    )
    built_in_tools: list[BuiltInTools] = Field(
        default_factory=list,
        description="Built in tools provided by the LLM model",
        json_schema_extra=UiSchema(widget=Widgets.built_in_tools, options_source=OptionsSource.built_in_tools),
    )
    tool_config: dict[str, ToolConfigModel] | None = Field(
        default_factory=dict,
        description="Configuration for builtin tools",
        json_schema_extra=UiSchema(widget=Widgets.none),
    )
    mcp_tools: list[str] = Field(
        default_factory=list,
        title="MCP Tools",
        description="MCP tools to enable for the bot",
        json_schema_extra=UiSchema(
            widget=Widgets.multiselect, options_source=OptionsSource.mcp_tools, flag_required="flag_mcp"
        ),
    )
    history_type: PipelineChatHistoryTypes = Field(
        PipelineChatHistoryTypes.GLOBAL,
        json_schema_extra=UiSchema(widget=Widgets.history, enum_labels=PipelineChatHistoryTypes.labels),
    )
    synthetic_voice_id: OptionalInt = Field(
        None, title="Voice Model", json_schema_extra=UiSchema(widget=Widgets.voice_widget)
    )

    @model_validator(mode="after")
    def check_prompt_variables(self) -> Self:
        context = {
            "prompt": self.prompt,
            "source_material": self.source_material_id,
            "tools": self.tools,
            "media": self.collection_id,
        }
        # Only require collection_index_summaries variable if multiple indexes are selected
        if len(self.collection_index_ids) > 1:
            context["collection_index_summaries"] = self.collection_index_ids

        try:
            known_vars = set(PromptVars.values) | PromptVars.pipeline_extra_known_vars()
            validate_prompt_variables(context=context, prompt_key="prompt", known_vars=known_vars)
            return self
        except ValidationError as e:
            raise PydanticCustomError(
                "invalid_prompt",
                e.error_dict["prompt"][0].message,  # ty: ignore[not-subscriptable]
                {"field": "prompt"},
            ) from None

    @field_validator("tools", "built_in_tools", "mcp_tools", mode="before")
    def ensure_value(cls, value: str):
        return value or []

    @field_validator("custom_actions", mode="before")
    def validate_custom_actions(cls, value):
        if value is None:
            return []
        return value

    @field_validator("collection_index_ids", mode="before")
    def validate_collection_index_ids(cls, value, info: FieldValidationInfo):
        if not value:
            return []

        # Ensure value is a list
        if not isinstance(value, list):
            value = [value]

        try:
            value = [int(v) for v in value]
        except (ValueError, TypeError) as e:
            raise PydanticCustomError(
                "invalid_collection_index",
                "Collection index IDs must be integers",
            ) from e

        # Filter out empty values
        value = [v for v in value if v is not None]
        if not value:
            return []

        # Get llm_provider_id from node data
        llm_provider_id = info.data.get("llm_provider_id")
        if not llm_provider_id:
            # If no LLM provider is set, we can't validate compatibility
            # This will be caught by other validation if llm_provider_id is required
            return value

        # Bulk fetch all collections to avoid N+1 queries
        repo = ORMRepository()
        collections = repo.get_collections_in_bulk(value)

        # Check for non-existent collections
        missing_ids = set(value) - set(collections.keys())
        if missing_ids:
            ids_str = ", ".join(str(missing_id) for missing_id in missing_ids)
            raise PydanticCustomError(
                "collection_not_found",
                f"Collection index(s) with ID(s) {ids_str} not found",
            )

        # Validate that all collections are the same type (either all remote or all local)
        # Only applies when multiple collections are selected
        is_remote_flags = [collection.is_remote_index for collection in collections.values()]
        all_are_remote = all(is_remote_flags)
        if not all_are_remote and any(is_remote_flags):
            remote_collections = [
                f"{collection.name}" for cid, collection in collections.items() if collection.is_remote_index
            ]
            local_collections = [
                f"{collection.name}" for cid, collection in collections.items() if not collection.is_remote_index
            ]
            raise PydanticCustomError(
                "mixed_collection_types",
                "All collection indexes must be the same type (either all remote or all local). "
                f"Remote collections: {', '.join(remote_collections)}. "
                f"Local collections: {', '.join(local_collections)}.",
            )

        # From this point on, we either have all remote or all local

        if all_are_remote:
            # Validate that all remote collections use the same LLM provider as this node
            incompatible_collections = [
                collection.name for collection in collections.values() if collection.llm_provider_id != llm_provider_id
            ]
            if incompatible_collections:
                raise PydanticCustomError(
                    "invalid_collection_index",
                    f"All remote collection indexes must use the same LLM provider as the node. "
                    f"Incompatible collections: {', '.join(incompatible_collections)}",
                )

            # Check if provider has a limit on number of vector stores
            try:
                llm_provider = repo.get_llm_provider(llm_provider_id)
            except RepositoryLookupError:
                llm_provider = None
            if llm_provider:
                max_vector_stores = llm_provider.type_enum.max_vector_stores
                if max_vector_stores and len(collections) > max_vector_stores:
                    raise PydanticCustomError(
                        "vectorstore_limit_exceeded",
                        f"{llm_provider.type_enum.value.label} hosted vectorstores are limited to "
                        f"{max_vector_stores} per request. "
                        f"You have selected {len(collections)} collection indexes. "
                        f"Please select at most {max_vector_stores} collection indexes.",
                    )

        if len(collections) > 1 and not all_are_remote:
            # local indexes must have a summary
            missing_summary = [collection.name for collection in collections.values() if not collection.summary]
            if missing_summary:
                raise PydanticCustomError(
                    "collections_missing_summary",
                    "When using multiple collection indexes, the collections must have a summary. "
                    f"Collections missing summary: {', '.join(missing_summary)}",
                )

        return value

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        return execute_sub_agent(self, context)


class SendEmail(PipelineNode, OutputMessageTagMixin):
    """Send the input to the node to the list of addresses provided"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Send an email",
            icon="fa-solid fa-envelope",
            documentation_link=settings.DOCUMENTATION_LINKS["node_email"],
        )
    )

    recipient_list: str = Field(description="A comma-separated list of email addresses")
    subject: str

    @field_validator("recipient_list", mode="before")
    def recipient_list_has_valid_emails(cls, value):
        value = value or ""
        for email in [email.strip() for email in value.split(",")]:
            try:
                validate_email(email)
            except ValidationError:
                raise PydanticCustomError("invalid_recipient_list", "Invalid list of emails addresses") from None
        return value

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        send_email_from_pipeline.delay(
            recipient_list=self.recipient_list.split(","), subject=self.subject, message=context.input
        )
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=context.input)


class Passthrough(PipelineNode):
    """Returns the input without modification"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Do Nothing", can_add=False))

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=context.input)


class StartNode(Passthrough):
    """The start of the pipeline"""

    name: str = "start"
    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Start", flow_node_type="startNode"))


class EndNode(Passthrough):
    """The end of the pipeline"""

    name: str = "end"
    model_config = ConfigDict(json_schema_extra=NodeSchema(label="End", flow_node_type="endNode"))


@deprecated_node(message="Use the 'Router' node instead.")
class BooleanNode(PipelineRouterNode):
    """Branches based whether the input matches a certain value"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Conditional Node"))

    input_equals: str
    tag_output_message: bool = Field(
        default=False,
        description="Tag the output message with the selected route",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )

    def _process_conditional(self, context: "NodeContext") -> tuple[Literal["true", "false"], bool]:
        if self.input_equals == context.input:
            return "true", False
        return "false", False

    def get_output_map(self):
        """A mapping from the output handles on the frontend to the return values of _process_conditional"""
        return {"output_0": "true", "output_1": "false"}

    def get_output_tags(self, selected_route, is_default_keyword: bool) -> list[tuple[str, str]]:  # ty: ignore[invalid-method-override]
        if self.tag_output_message:
            tag_name = f"{self.name}:{selected_route}"
            tag_category = TagCategories.ERROR if is_default_keyword else TagCategories.BOT_RESPONSE
            if is_default_keyword:
                tag_name += ":default"
            return [(tag_name, tag_category)]
        return []


class RouterNode(RouterMixin, PipelineRouterNode, HistoryMixin):
    """Routes the input to one of the linked nodes using an LLM"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="LLM Router",
            icon="fa-solid fa-arrows-split-up-and-left",
            documentation_link=settings.DOCUMENTATION_LINKS["node_llm_router"],
            field_order=[
                "llm_provider_id",
                "llm_temperature",
                "history_type",
                "prompt",
                "keywords",
                "tag_output_message",
            ],
        )
    )
    prompt: str = Field(
        default="You are an extremely helpful router",
        min_length=1,
        json_schema_extra=UiSchema(
            widget=Widgets.text_editor, options_source=OptionsSource.text_editor_autocomplete_vars_router_node
        ),
    )
    history_type: PipelineChatHistoryTypes = Field(
        PipelineChatHistoryTypes.NODE,
        json_schema_extra=UiSchema(widget=Widgets.history, enum_labels=PipelineChatHistoryTypes.labels),
    )

    @model_validator(mode="after")
    def check_prompt_variables(self) -> Self:
        context = {
            "prompt": self.prompt,
        }
        try:
            known_vars = {PromptVars.PARTICIPANT_DATA.value} | PromptVars.pipeline_extra_known_vars()
            validate_prompt_variables(context=context, prompt_key="prompt", known_vars=known_vars)
            return self
        except ValidationError as e:
            raise PydanticCustomError(
                "invalid_prompt",
                e.error_dict["prompt"][0].message,  # ty: ignore[not-subscriptable]
                {"field": "prompt"},
            ) from None

    def _process_conditional(self, context: "NodeContext"):
        default_keyword = self.keywords[self.default_keyword_index] if self.keywords else None
        node_input = context.input
        session = context.session
        extra_prompt_context = {
            "temp_state": context.state.temp,
            "session_state": context.state.session_state,
        }
        participant_data = context.state.participant_data or {}
        template_context = PromptTemplateContext(
            session, extra=extra_prompt_context, participant_data=participant_data, repo=self.repo
        )
        system_message = get_system_message(
            prompt_template=f"{self.prompt}\nThe default routing destination is: {default_keyword}",
            prompt_context=template_context,
        )

        # Build the agent
        middleware = []
        if history_middleware := self.build_history_middleware(session=session, system_message=system_message):
            middleware.append(history_middleware)

        agent = create_agent(
            model=self.get_chat_model(),
            system_prompt=system_message,
            middleware=middleware,
            response_format=self._create_router_schema(),
        )

        is_default_keyword = False
        try:
            agent_input = {"messages": [HumanMessage(content=node_input)]}
            result = agent.invoke(agent_input, config=self._config)
            structured_response = result["structured_response"]
            keyword = structured_response.route.upper()  # ensure case-insensitive matching
        except PydanticValidationError:
            keyword = None
        except OpenAIRefusalError:
            keyword = default_keyword
            is_default_keyword = True
        except StructuredOutputValidationError:
            logger.exception("Structured output validation error in RouterNode")
            keyword = None

        if not keyword:
            keyword = default_keyword
            is_default_keyword = True

        if session:
            self.save_history(session, node_input, keyword)
        return keyword, is_default_keyword


class StaticRouterNode(RouterMixin, PipelineRouterNode):
    """Routes the input to a linked node using the temp state of the pipeline or participant data"""

    class DataSource(TextChoices):
        participant_data = "participant_data", "Participant Data"
        temp_state = "temp_state", "Temporary State"
        session_state = "session_state", "Session State"

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Static Router",
            icon="fa-solid fa-arrows-split-up-and-left",
            documentation_link=settings.DOCUMENTATION_LINKS["node_static_router"],
            field_order=["data_source", "route_key", "keywords"],
        )
    )

    data_source: DataSource = Field(
        DataSource.participant_data,
        description="The source of the data to use for routing",
        json_schema_extra=UiSchema(enum_labels=DataSource.labels),
    )
    route_key: str = Field(..., description="The key in the data to use for routing")

    def _process_conditional(self, context: "NodeContext"):
        from apps.service_providers.llm_service.prompt_context import SafeAccessWrapper

        match self.data_source:
            case self.DataSource.participant_data:
                data = context.state.participant_data
            case self.DataSource.temp_state:
                data = context.state.temp or {}
            case self.DataSource.session_state:
                data = context.state.session_state or {}

        formatted_key = f"{{data.{self.route_key}}}"
        try:
            result = formatted_key.format(data=SafeAccessWrapper(data))
        except KeyError:
            result = ""

        result_upper = result.upper()
        for keyword in self.keywords:
            if keyword == result_upper:
                return keyword, False
        return self.keywords[self.default_keyword_index], True


class ExtractStructuredData(
    ExtractStructuredDataNodeMixin, LLMResponse, StructuredDataSchemaValidatorMixin, OutputMessageTagMixin
):
    """Extract structured data from the input"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Extract Structured Data",
            icon="fa-solid fa-database",
            documentation_link=settings.DOCUMENTATION_LINKS["node_extract_structured_data"],
        )
    )

    data_schema: str = Field(
        default='{"name": "the name of the user"}',
        description="A JSON object structure where the key is the name of the field and the value the description",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )

    def get_node_output(self, context, output_data) -> PipelineState:
        output = json.dumps(output_data)
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output)


class ExtractParticipantData(
    ExtractStructuredDataNodeMixin, LLMResponse, StructuredDataSchemaValidatorMixin, OutputMessageTagMixin
):
    """Extract structured data and saves it as participant data"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Update Participant Data",
            icon="fa-solid fa-user-pen",
            documentation_link=settings.DOCUMENTATION_LINKS["node_update_participant_data"],
        )
    )

    data_schema: str = Field(
        default='{"name": "the name of the user"}',
        description="A JSON object structure where the key is the name of the field and the value the description",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )
    key_name: str = ""

    def get_reference_data(self, context) -> Any:
        """Returns the participant data as reference. If there is a `key_name`, the value in the participant data
        corresponding to that key will be returned insteadg
        """
        data = context.state.participant_data or {}
        if self.key_name:
            return data.get(self.key_name, "")
        return data

    def update_reference_data(self, new_data: dict, reference_data: dict | list | str) -> dict:
        if isinstance(reference_data, dict):
            # new_data may be a subset, superset or wholly different set of keys than the reference_data, so merge
            return reference_data | new_data

        # if reference data is a string or list, we cannot merge, so let's override
        return new_data

    def get_node_output(self, context, output_data) -> PipelineState:
        if self.key_name:
            output_data = {self.key_name: output_data}

        return PipelineState.from_node_output(
            node_name=self.name, node_id=self.node_id, output=context.input, participant_data=output_data
        )


@deprecated_node(message="Use the 'LLM' node instead.", docs_link="migrate_from_assistant")
class AssistantNode(PipelineNode, OutputMessageTagMixin):
    """Calls an OpenAI assistant"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="OpenAI Assistant",
            icon="fa-solid fa-user-tie",
            documentation_link=settings.DOCUMENTATION_LINKS["node_assistant"],
        )
    )

    assistant_id: int = Field(
        ..., json_schema_extra=UiSchema(widget=Widgets.select, options_source=OptionsSource.assistant)
    )
    citations_enabled: bool = Field(
        default=True,
        description="Whether to include cited sources in responses",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )
    input_formatter: str = Field("", description="(Optional) Use {input} to designate the user input")

    @field_validator("input_formatter")
    def ensure_input_variable_exists(cls, value):
        value = value or ""
        acceptable_var = "input"
        if value:
            prompt_variables = set(PromptTemplate.from_template(value).input_variables)
            if acceptable_var not in prompt_variables:
                raise PydanticCustomError("invalid_input_formatter", "The input formatter must contain {input}")

            acceptable_vars = set([acceptable_var])
            extra_vars = prompt_variables - acceptable_vars
            if extra_vars:
                raise PydanticCustomError("invalid_input_formatter", "Only {input} is allowed")

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        try:
            assistant = self.repo.get_assistant(self.assistant_id)
        except RepositoryLookupError:
            raise PipelineNodeBuildError(f"Assistant {self.assistant_id} does not exist") from None

        session = context.session
        runnable = self._get_assistant_runnable(assistant, session=session)
        attachments = [att for att in context.attachments if att.upload_to_assistant]
        chain_output: ChainOutput = runnable.invoke(context.input, config=self._config, attachments=attachments)
        output = chain_output.output

        return PipelineState.from_node_output(
            node_name=self.name,
            node_id=self.node_id,
            output=output,
            input_message_metadata=runnable.history_manager.input_message_metadata or {},
            output_message_metadata=runnable.history_manager.output_message_metadata or {},
        )

    def _get_assistant_runnable(self, assistant: OpenAiAssistant, session: ExperimentSession):
        history_manager = AssistantPipelineHistoryManager()
        adapter = AssistantAdapter.for_pipeline(session=session, node=self, disabled_tools=self.disabled_tools)

        if adapter.get_allowed_tools():
            return AgentAssistantChat(adapter=adapter, history_manager=history_manager)
        else:
            if assistant.tools_enabled:
                logging.info("Tools have been disabled")
            return AssistantChat(adapter=adapter, history_manager=history_manager)


class CodeNode(PipelineNode, OutputMessageTagMixin, RestrictedPythonExecutionMixin):
    """Runs python"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Python Node",
            icon="fa-solid fa-file-code",
            documentation_link=settings.DOCUMENTATION_LINKS["node_code"],
        )
    )
    code: str = Field(
        default=DEFAULT_FUNCTION,
        description="The code to run",
        json_schema_extra=UiSchema(widget=Widgets.code),
    )

    @classmethod
    def _get_default_code(cls) -> str:
        return DEFAULT_FUNCTION

    @classmethod
    def _get_function_args(cls) -> list[str]:
        return ["input", "**kwargs"]

    @field_validator("code", mode="before")
    def check_reserved_session_state_keys(cls, value: str):
        for key in settings.RESERVED_SESSION_STATE_KEYS:
            pattern = re.compile(rf'set_session_state_key\([\s*"\']*{re.escape(key)}[\s*"\']')
            if pattern.search(value):
                raise PydanticCustomError(
                    "reserved_key_used",
                    f"The key '{key}' is a reserved session state key and is read-only.",
                )
        return value

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState | Command:
        output_state = PipelineState()
        try:
            result = self.compile_and_execute_code(
                additional_globals=self._get_custom_functions(state, context, output_state),
                input=context.input,
                node_inputs=context.inputs,
            )
        except WaitForNextInput:
            return Command(goto=END)
        except AbortPipeline as abort:
            return interrupt(abort.to_json())
        except Exception as exc:
            message = get_code_error_message("<inline_code>", self.code)
            raise CodeNodeRunError(message) from exc

        if isinstance(result, Command):
            return result
        return Command(
            goto=self._outgoing_nodes,
            update=PipelineState.from_node_output(
                node_name=self.name, node_id=self.node_id, output=str(result), **output_state
            ),
        )

    def _get_custom_functions(self, state: PipelineState, context: "NodeContext", output_state: PipelineState) -> dict:
        """
        Args:
            state: The input state. Do not modify this state.
            context: The NodeContext for read access.
            output_state: An empty state dict to which state modifications should be made.
        """
        pipeline_state = PipelineState.clone(state)

        # copy this from input to output to create a consistent view within the code execution
        output_state["temp_state"] = pipeline_state.get("temp_state") or {}
        output_state["participant_data"] = pipeline_state.get("participant_data") or {}
        output_state["session_state"] = pipeline_state.get("session_state") or {}

        # use 'output_state' so that we capture any updates
        participant_data_proxy = PipelineParticipantDataProxy(output_state, context.session, repo=self.repo)

        # add this node into the state so that we can trace the path
        pipeline_state["outputs"] = {**state["outputs"], self.name: {"node_id": self.node_id}}
        session = context.session
        team = self.repo.get_session_team(session) if session else None

        http_client = RestrictedHttpClient(team=team)

        # We create a PipelineAccessor wrapping a *cloned* state with the current node
        # injected into outputs (line above). The clone also isolates user code mutations
        # from the real pipeline state. This is an intentional bypass of the NodeContext
        # abstraction for CodeNode's sandboxed execution environment.
        pipeline_accessor = PipelineAccessor(pipeline_state)
        return {
            "http": http_client,
            "get_participant_data": participant_data_proxy.get,
            "set_participant_data": participant_data_proxy.set,
            "set_participant_data_key": participant_data_proxy.set_key,
            "append_to_participant_data_key": participant_data_proxy.append_to_key,
            "increment_participant_data_key": participant_data_proxy.increment_key,
            "get_participant_schedules": participant_data_proxy.get_schedules,
            "get_temp_state_key": self._get_temp_state_key(output_state),
            "set_temp_state_key": self._set_temp_state_key(output_state),
            "get_session_state_key": self._get_session_state_key(output_state),
            "set_session_state_key": self._set_session_state_key(output_state),
            "get_selected_route": pipeline_accessor.get_selected_route,
            "get_node_path": pipeline_accessor.get_node_path,
            "get_all_routes": pipeline_accessor.get_all_routes,
            "add_file_attachment": self._add_file_attachment(context, output_state),
            "add_message_tag": output_state.add_message_tag,
            "add_session_tag": output_state.add_session_tag,
            "get_node_output": pipeline_accessor.get_node_output,
            # control flow
            "abort_with_message": self._abort_pipeline(),
            "require_node_outputs": self._require_node_outputs(context),
            "wait_for_next_input": self.wait_for_next_input,
        }

    def _add_file_attachment(self, context, output_state: PipelineState):
        def add_file_attachment(filename: str, content: bytes, content_type: str | None = None):
            """Attach a file to the AI response message.

            Args:
                filename: The name of the file (e.g. "report.pdf")
                content: The file content as bytes
                content_type: Optional MIME type. Auto-detected from filename if not provided.
            """
            from io import BytesIO

            if not isinstance(content, bytes):
                raise CodeNodeRunError("'content' must be bytes")

            session = context.session
            if not session:
                raise CodeNodeRunError("Cannot attach files without an active session")

            file_obj = BytesIO(content)
            team = self.repo.get_session_team(session)
            if not team:
                raise CodeNodeRunError("Cannot attach files without a valid session team")

            file = self.repo.create_file(
                filename=filename,
                file_obj=file_obj,
                team_id=team.id,
                content_type=content_type,
                purpose=FilePurpose.MESSAGE_MEDIA,
            )

            self.repo.attach_files_to_chat(session=session, attachment_type="code_interpreter", files=[file])

            metadata = output_state.setdefault("output_message_metadata", {})
            generated_files = metadata.setdefault("generated_files", [])
            generated_files.append(file.id)

        return add_file_attachment

    def _abort_pipeline(self):
        def abort_pipeline(message, tag_name: str | None = None):
            """Calling this will terminate the pipeline execution. No further nodes will get executed in
            any branch of the pipeline graph.

            The message provided will be used to notify the user about the reason for the termination.
            If a tag name is provided, it will be used to tag the output message."""
            raise AbortPipeline(message, tag_name)

        return abort_pipeline

    def _require_node_outputs(self, context):
        """A helper function to require inputs from a specific node"""

        def require_node_outputs(*node_names):
            """This function is used to ensure that the specified nodes have been executed and their outputs
            are available in the pipeline's state. If any of the specified nodes have not been executed,
            the node will not execute and the pipeline will wait for the required nodes to complete.

            This should be called at the start of the main function."""
            if len(node_names) == 1 and isinstance(node_names[0], list):
                node_names = node_names[0]
            if not all(isinstance(name, str) for name in node_names):
                raise CodeNodeRunError("Node names passed to 'require_node_outputs' must be a string")
            for node_name in node_names:
                if not context.pipeline.has_node_output(node_name):
                    raise WaitForNextInput(f"Node '{node_name}' has not produced any output yet")

        return require_node_outputs

    def wait_for_next_input(self):
        """Advanced utility that will abort the current execution. This is similar to `require_node_outputs` but
        used where some node outputs may be optional."""
        raise WaitForNextInput("Waiting for next input")

    def _get_session_state_key(self, state: PipelineState):
        def get_session_state_key(key_name: str):
            """Returns the value of the session state's key with the given name.
            If the key does not exist, it returns `None`."""
            return state.get("session_state", {}).get(key_name)

        return get_session_state_key

    def _set_session_state_key(self, state: PipelineState):
        def set_session_state_key(key_name: str, value):
            """Sets the value of the session state's key with the given name to the provided data.
            This will override any existing data."""
            session_state = state.setdefault("session_state", {})
            session_state[key_name] = value

        return set_session_state_key

    def _get_temp_state_key(self, state: PipelineState):
        def get_temp_state_key(key_name: str):
            """Returns the value of the temporary state key with the given name.
            If the key does not exist, it returns `None`."""
            return state["temp_state"].get(key_name)

        return get_temp_state_key

    def _set_temp_state_key(self, state: PipelineState):
        def set_temp_state_key(key_name: str, value):
            """Sets the value of the temporary state key with the given name to the provided data.
            This will override any existing data for the key unless the key is read-only, in which case
            an error will be raised. Read-only keys are: `user_input`, `outputs`, `attachments`."""
            if key_name in {"user_input", "outputs", "attachments"}:
                raise CodeNodeRunError(f"Cannot set the '{key_name}' key of the temporary state")
            state["temp_state"][key_name] = value

        return set_temp_state_key

from __future__ import annotations

import textwrap
from functools import cached_property
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from apps.annotations.models import TagCategories
from apps.chat.conversation import BasicConversation
from apps.chat.exceptions import ChatException
from apps.chat.models import ChatMessage, ChatMessageType
from apps.events.models import StaticTriggerType
from apps.experiments.models import Experiment, ExperimentSession, ParticipantData
from apps.pipelines.executor import CurrentThreadExecutor, DjangoLangGraphRunner, DjangoSafeContextThreadPoolExecutor
from apps.pipelines.nodes.base import Intents, PipelineState
from apps.service_providers.llm_service.default_models import get_default_model, get_model_parameters
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.service_providers.tracing.base import SpanNotificationConfig
from apps.web.search import get_global_search_url

if TYPE_CHECKING:
    from apps.channels.datamodels import Attachment
    from apps.experiments.models import SyntheticVoice


def create_conversation(
    prompt_str: str,
    source_material: str,
    llm: BaseChatModel,
) -> BasicConversation:
    try:
        return BasicConversation(
            prompt_str=prompt_str,
            source_material=source_material,
            llm=llm,
        )
    except ValidationError as e:
        raise ChatException(str(e)) from e


def get_bot(session: ExperimentSession, experiment: Experiment, trace_service, disable_tools: bool = False):
    experiment = experiment or session.experiment_version
    if experiment.pipeline_id:
        return PipelineBot(session, experiment, trace_service, disable_reminder_tools=disable_tools)
    raise NotImplementedError("Only pipeline chatbots are supported")


class PipelineBot:
    def __init__(self, session: ExperimentSession, experiment: Experiment, trace_service, disable_reminder_tools=False):
        self.team = experiment.team
        self.experiment = experiment
        self.session = session
        self.trace_service = trace_service
        self.disable_reminder_tools = disable_reminder_tools
        self.synthetic_voice_id = None

    def process_input(
        self,
        user_input: str,
        attachments: list[Attachment] | None = None,
        human_message: ChatMessage | None = None,
    ) -> ChatMessage:
        input_state = self._get_input_state(attachments, user_input)

        if human_message:
            input_state["input_message_id"] = human_message.id
            input_state["input_message_url"] = get_global_search_url(human_message)

        with self.trace_service.span(
            "Run Pipeline",
            inputs={"input_state": input_state.json_safe()},
            notification_config=SpanNotificationConfig(permissions=["experiments.change_experiment"]),
        ) as span:
            ai_message = self.invoke_pipeline(
                input_state=input_state, human_message=human_message, save_run_to_history=True
            )
            span.set_outputs({"content": ai_message.content})
            return ai_message

    def invoke_pipeline(
        self,
        input_state: PipelineState,
        save_run_to_history=True,
        pipeline=None,
        human_message: ChatMessage | None = None,
    ) -> ChatMessage:
        pipeline_to_use = pipeline or self.experiment.pipeline

        output = self._run_pipeline(input_state, pipeline_to_use)

        if save_run_to_history and self.session is not None:
            output = self._process_interrupts(output)
            ai_message = self._save_outputs(
                input_state,
                output,
                human_message=human_message,
            )
        else:
            ai_message = ChatMessage(content=output)
        self._process_intents(output)
        self.synthetic_voice_id = output.get("synthetic_voice_id", None)
        return ai_message

    def _get_input_state(self, attachments: list[Attachment], user_input: str):
        state = PipelineState(
            messages=[user_input],
            experiment_session=self.session,
            session_state=self.session.state,
        )
        self._update_state_with_participant_data(state)
        self._updates_state_with_attachments(state, attachments)
        return state

    def _update_state_with_participant_data(self, state):
        data = self.participant_data.data | {}
        data = self.session.participant.global_data | data
        state["participant_data"] = data
        return state

    def _updates_state_with_attachments(self, state: PipelineState, attachments: list[Attachment]):
        attachments = attachments or []
        state["input_message_metadata"] = {}
        if attachments:
            state["input_message_metadata"]["ocs_attachment_file_ids"] = [
                attachment.file_id for attachment in attachments
            ]
            state["attachments"] = [attachment.model_dump() for attachment in attachments]
        return state

    def _run_pipeline(self, input_state, pipeline_to_use):
        from apps.experiments.models import AgentTools
        from apps.pipelines.graph import PipelineGraph

        graph = PipelineGraph.build_from_pipeline(pipeline_to_use)
        config = self.trace_service.get_langchain_config(
            configurable={
                "disabled_tools": AgentTools.reminder_tools() if self.disable_reminder_tools else [],
            },
            run_name_map=graph.node_id_to_name_mapping,
            filter_patterns=graph.filter_patterns,
        )
        runnable = graph.build_runnable()
        runner = DjangoLangGraphRunner(DjangoSafeContextThreadPoolExecutor)
        raw_output = runner.invoke(runnable, input_state, config)
        output = PipelineState(**raw_output).json_safe()
        return output

    def _process_interrupts(self, output):
        if interrupt := output.get("interrupt"):
            trace_info = TraceInfo(name="interrupt", metadata={"interrupt": interrupt})
            output_message = EventBot(
                session=self.session,
                experiment=self.experiment,
                trace_info=trace_info,
                trace_service=self.trace_service,
            ).get_user_message(interrupt["message"])
            output["messages"].append(output_message)
            if tag_name := interrupt["tag_name"]:
                tags = output.setdefault("output_message_tags", [])
                tags.append((TagCategories.SAFETY_LAYER_RESPONSE, tag_name))
        return output

    def _save_outputs(
        self,
        input_state,
        output,
        human_message=None,
    ):
        input_metadata = output.get("input_message_metadata", {})
        output_metadata = output.get("output_message_metadata", {})
        trace_metadata = self.trace_service.get_trace_metadata() if self.trace_service else None
        if trace_metadata:
            output_metadata.update(trace_metadata)

        if human_message:
            if input_metadata != input_state.get("input_message_metadata"):
                human_message.metadata.update(input_metadata)
                human_message.save(update_fields=["metadata"])

        output_tags = output.get("output_message_tags")
        ai_message = self._save_message_to_history(
            output["messages"][-1],
            ChatMessageType.AI,
            metadata=output_metadata,
            tags=output_tags,
        )
        if self.trace_service:
            self.trace_service.set_output_message_id(ai_message.id)
        ai_message.add_version_tag(
            version_number=self.experiment.version_number, is_a_version=self.experiment.is_a_version
        )
        if self.trace_service and output_tags:
            flat_tags = [f"{category}:{tag}" if category else tag for tag, category in output_tags]
            self.trace_service.add_output_message_tags_to_trace(flat_tags)

        if session_tags := output.get("session_tags"):
            for tag, category in session_tags:
                self.session.chat.create_and_add_tag(tag, self.session.team, tag_category=category)

        out_pd = output.get("participant_data", None)
        if out_pd is not None and out_pd != input_state.get("participant_data"):
            self.participant_data.data = out_pd
            self.participant_data.save(update_fields=["data"])
            self.session.participant.update_name_from_data(out_pd)

        out_session_state = output.get("session_state", None)
        if out_session_state is not None and out_session_state != input_state.get("session_state"):
            self.session.state = out_session_state
            self.session.save(update_fields=["state"])
        return ai_message

    def _process_intents(self, pipeline_output: dict):
        for intent in pipeline_output.get("intents", []):
            match intent:
                case Intents.END_SESSION:
                    self.session.end(trigger_type=StaticTriggerType.CONVERSATION_ENDED_BY_BOT)

    def _save_message_to_history(
        self,
        message: str,
        type_: ChatMessageType,
        metadata: dict,
        tags: list[tuple] = None,
    ) -> ChatMessage:
        chat_message = ChatMessage.objects.create(
            chat=self.session.chat, message_type=type_.value, content=message, metadata=metadata
        )

        if tags:
            for tag_value, category in tags:
                chat_message.create_and_add_tag(tag_value, self.session.team, category or "")
        return chat_message

    def get_synthetic_voice(self) -> SyntheticVoice | None:
        from apps.experiments.models import SyntheticVoice

        if self.synthetic_voice_id is None:
            return None
        return SyntheticVoice.objects.filter(
            id=self.synthetic_voice_id, service__iexact=self.experiment.voice_provider.type
        ).first()

    @cached_property
    def participant_data(self):
        participant_data, _ = ParticipantData.objects.get_or_create(
            participant_id=self.session.participant_id,
            experiment_id=self.session.experiment_id,
            team_id=self.session.team_id,
        )
        return participant_data


class EvalsBot(PipelineBot):
    def __init__(self, session: ExperimentSession, experiment: Experiment, trace_service, participant_data: dict):
        super().__init__(session, experiment, trace_service, False)
        self._participant_data = participant_data

    def _update_state_with_participant_data(self, state):
        state["participant_data"] = self._participant_data
        return state


class PipelineTestBot:
    """Invoke the pipeline with a temporary session or the ability to save the run to history"""

    def __init__(self, pipeline, user_id: int):
        self.pipeline = pipeline
        self.user_id = user_id

    def process_input(self, input: str) -> PipelineState:
        from apps.pipelines.graph import PipelineGraph
        from apps.pipelines.nodes.helpers import temporary_session

        with temporary_session(self.pipeline.team, self.user_id) as session:
            runnable = PipelineGraph.build_runnable_from_pipeline(self.pipeline)
            input = PipelineState(messages=[input], experiment_session=session)
            runner = DjangoLangGraphRunner(CurrentThreadExecutor)
            output = runner.invoke(runnable, input)
            output = PipelineState(**output).json_safe()
        return output


class EventBot:
    SYSTEM_PROMPT = textwrap.dedent(
        """
        Your role is to generate messages to send to users. These could be reminders
        or prompts to help them complete their tasks or error messages. The text that you generate will be sent
        to the user in a chat message.

        <example>
        Input: Remember to brush your teeth.
        Output: Don't forget to brush your teeth.
        </example>
        <example>
        Input: Remind me about my appointment with Dr Niel at 5pm.
        Output: Here is your reminder for your appointment with Dr Niel at 5pm.
        </example>
        <example>
        Input: The message was inappropriate.
        Output: Unfortunately I can't respond to your last message because it goes against my usage policy.
        </example>

        You should generate the message in same language as the recent conversation history shown below.
        If there is no history use English.

        #### Conversation history
        {conversation_history}

        #### User data
        {participant_data}

        #### Current date and time
        {current_datetime}

        Output only the final message, no additional text. Do not put the message in quotes.
        """
    )

    def __init__(
        self,
        session: ExperimentSession,
        experiment: Experiment,
        trace_info: TraceInfo,
        history_manager=None,
        trace_service=None,
    ):
        self.session = session
        self.experiment = experiment or session.experiment_version
        self.history_manager = history_manager
        self.trace_info = trace_info
        self.trace_service = trace_service

    def get_user_message(self, event_prompt: str) -> str:
        provider = self.llm_provider
        if not provider:
            raise Exception("No LLM provider found")

        model = get_default_model(provider.type)
        params = get_model_parameters(model.name, temperature=0.7)

        service = provider.get_llm_service()
        llm = service.get_chat_model(model.name, **params)

        if not self.trace_service:
            self.trace_service = (
                self.history_manager.trace_service
                if self.history_manager
                else TracingService.create_for_experiment(self.experiment)
            )

        with self.trace_service.trace_or_span(
            name=f"{self.experiment.name} - {self.trace_info.name}",
            session=self.session,
            inputs={"input": event_prompt},
            metadata=self.trace_info.metadata,
        ) as span:
            config = self.trace_service.get_langchain_config()
            response = llm.invoke(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": event_prompt},
                ],
                config=config,
            )
            message = response.text
            span.set_outputs({"response": message})

            if self.history_manager:
                self.history_manager.save_message_to_history(message, type_=ChatMessageType.AI)
        return message

    @property
    def llm_provider(self):
        return self.experiment.team.llmprovider_set.first()

    @property
    def system_prompt(self):
        context = PromptTemplateContext(self.session, None).get_context(["participant_data", "current_datetime"])
        context["conversation_history"] = self.get_conversation_history()
        return self.SYSTEM_PROMPT.format(**context)

    def get_conversation_history(self):
        messages = []
        for message in self.session.chat.message_iterator(with_summaries=False):
            messages.append(f"{message.role}: {message.content}")
            if len(messages) > 10:
                break
        if messages:
            return textwrap.dedent(
                """
                Here are the most recent messages in the conversation:
                ```
                {}
                ```
                """
            ).format("\n".join(reversed(messages)))
        else:
            return "\nThis is the start of the conversation so there is no previous message history"

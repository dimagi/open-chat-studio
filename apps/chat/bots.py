from typing import TYPE_CHECKING, Any

from langchain.chat_models.base import BaseChatModel
from langchain.memory import ConversationBufferMemory
from pydantic import ValidationError

from apps.chat.conversation import BasicConversation, Conversation
from apps.chat.exceptions import ChatException
from apps.events.models import StaticTriggerType
from apps.events.tasks import enqueue_static_triggers
from apps.experiments.models import Experiment, ExperimentRoute, ExperimentSession, SafetyLayer
from apps.service_providers.llm_service.runnables import create_experiment_runnable

if TYPE_CHECKING:
    from apps.channels.datamodels import Attachment


def create_conversation(
    prompt_str: str,
    source_material: str,
    llm: BaseChatModel,
) -> Conversation:
    try:
        return BasicConversation(
            prompt_str=prompt_str,
            source_material=source_material,
            memory=ConversationBufferMemory(return_messages=True),
            llm=llm,
        )
    except ValidationError as e:
        raise ChatException(str(e)) from e


def notify_users_of_violation(session_id: int, safety_layer_id: int):
    from apps.chat.tasks import notify_users_of_safety_violations_task

    notify_users_of_safety_violations_task.delay(session_id, safety_layer_id)


class TopicBot:
    """
    Parameters
    ----------
    session:
        The session to provide the chat history. New messages will be saved to this session.
    experiment: (optional)
        The experiment to provide the source material and other data for the LLM.
        NOTE: Only use this if you know what you are doing. Normally this should be left empty, in which case
        the session's own experiment will be used. This is used in a multi-bot setup where the user might want
        a specific bot to handle a scheduled message, in which case it would be useful for the LLM to have the
        conversation history of the participant's chat with the router / main bot.
    """

    def __init__(self, session: ExperimentSession, experiment: Experiment | None = None):
        self.experiment = experiment or session.experiment
        self.prompt = self.experiment.prompt_text
        self.input_formatter = self.experiment.input_formatter
        self.llm = self.experiment.get_chat_model()
        self.source_material = self.experiment.source_material.material if self.experiment.source_material else None
        self.safety_layers = self.experiment.safety_layers.all()
        self.chat = session.chat
        self.session = session
        self.max_token_limit = self.experiment.max_token_limit
        self.input_tokens = 0
        self.output_tokens = 0

        # maps keywords to child experiments.
        self.child_experiment_routes = (
            ExperimentRoute.objects.select_related("child").filter(parent=self.experiment, type="processor").all()
        )
        self.child_chains = {}
        self.default_child_chain = None
        self.default_tag = None
        self.terminal_chain = None

        self._initialize()

    def _initialize(self):
        for child_route in self.child_experiment_routes:
            child_runnable = create_experiment_runnable(child_route.child, self.session)
            self.child_chains[child_route.keyword.lower().strip()] = child_runnable
            if child_route.is_default:
                self.default_child_chain = child_runnable
                self.default_tag = child_route.keyword.lower().strip()

        if self.child_chains and not self.default_child_chain:
            self.default_tag, self.default_child_chain = list(self.child_chains.items())[0]

        self.chain = create_experiment_runnable(self.experiment, self.session)

        terminal_route = (
            ExperimentRoute.objects.select_related("child").filter(parent=self.experiment, type="terminal").first()
        )
        if terminal_route:
            self.terminal_chain = create_experiment_runnable(terminal_route.child, self.session)

        # load up the safety bots. They should not be agents. We don't want them using tools (for now)
        self.safety_bots = [
            SafetyBot(safety_layer, self.llm, self.source_material) for safety_layer in self.safety_layers
        ]

    def _call_predict(self, input_str, save_input_to_history=True, attachments: list["Attachment"] | None = None):
        if self.child_chains:
            tag, chain = self._get_child_chain(input_str, attachments)
        else:
            tag, chain = None, self.chain

        result = chain.invoke(
            input_str,
            config={
                "configurable": {
                    "save_input_to_history": save_input_to_history,
                    "save_output_to_history": self.terminal_chain is None,
                    "experiment_tag": tag,
                }
            },
            attachments=attachments,
        )

        if self.terminal_chain:
            result = self.terminal_chain.invoke(
                result.output,
                config={
                    "configurable": {
                        "save_input_to_history": False,
                        "experiment_tag": tag,
                        "include_conversation_history": False,
                    }
                },
            )

        enqueue_static_triggers.delay(self.session.id, StaticTriggerType.NEW_BOT_MESSAGE)
        self.input_tokens = self.input_tokens + result.prompt_tokens
        self.output_tokens = self.output_tokens + result.completion_tokens
        return result.output

    def _get_child_chain(self, input_str: str, attachments: list["Attachment"] | None = None) -> tuple[str, Any]:
        result = self.chain.invoke(
            input_str,
            config={
                "configurable": {
                    "save_input_to_history": False,
                    "save_output_to_history": False,
                }
            },
            attachments=attachments,
        )
        self.input_tokens = self.input_tokens + result.prompt_tokens
        self.output_tokens = self.output_tokens + result.completion_tokens

        keyword = result.output.lower().strip()
        try:
            return keyword, self.child_chains[keyword]
        except KeyError:
            return self.default_tag, self.default_child_chain

    def fetch_and_clear_token_count(self):
        safety_bot_input_tokens = sum([bot.input_tokens for bot in self.safety_bots])
        safety_bot_output_tokens = sum([bot.output_tokens for bot in self.safety_bots])
        input_tokens = self.input_tokens + safety_bot_input_tokens
        output_tokens = self.output_tokens + safety_bot_output_tokens
        self.input_tokens = 0
        self.output_tokens = 0
        for bot in self.safety_bots:
            bot.input_tokens = 0
            bot.output_tokens = 0
        return input_tokens, output_tokens

    def process_input(self, user_input: str, save_input_to_history=True, attachments: list["Attachment"] | None = None):
        # human safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_human_messages() and not safety_bot.is_safe(user_input):
                enqueue_static_triggers.delay(self.session.id, StaticTriggerType.HUMAN_SAFETY_LAYER_TRIGGERED)
                notify_users_of_violation(self.session.id, safety_layer_id=safety_bot.safety_layer.id)
                return self._get_safe_response(safety_bot.safety_layer)

        response = self._call_predict(user_input, save_input_to_history=save_input_to_history, attachments=attachments)

        # ai safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_ai_messages() and not safety_bot.is_safe(response):
                enqueue_static_triggers.delay(self.session.id, StaticTriggerType.BOT_SAFETY_LAYER_TRIGGERED)
                return self._get_safe_response(safety_bot.safety_layer)

        return response

    def _get_safe_response(self, safety_layer: SafetyLayer):
        if safety_layer.prompt_to_bot:
            safety_response = self._call_predict(safety_layer.prompt_to_bot, save_input_to_history=False)
            return safety_response
        else:
            no_answer = "Sorry, I can't answer that. Please try something else."
            return safety_layer.default_response_to_user or no_answer


class SafetyBot:
    def __init__(self, safety_layer: SafetyLayer, llm: BaseChatModel, source_material: str | None):
        self.safety_layer = safety_layer
        self.prompt = safety_layer.prompt_text
        self.llm = llm
        self.source_material = source_material
        self.input_tokens = 0
        self.output_tokens = 0
        self._initialize()

    def _initialize(self):
        self.conversation = create_conversation(self.prompt, self.source_material, self.llm)

    def _call_predict(self, input_str):
        response, prompt_tokens, completion_tokens = self.conversation.predict(input=input_str)
        self.input_tokens = self.input_tokens + prompt_tokens
        self.output_tokens = self.output_tokens + completion_tokens
        return response

    def is_safe(self, input_str: str) -> bool:
        result = self._call_predict(input_str)
        if result.strip().lower().startswith("safe"):
            return True
        elif result.strip().lower().startswith("unsafe"):
            return False
        else:
            return False

    def filter_human_messages(self) -> bool:
        return self.safety_layer.messages_to_review == "human"

    def filter_ai_messages(self) -> bool:
        return self.safety_layer.messages_to_review == "ai"

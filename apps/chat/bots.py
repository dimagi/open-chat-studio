from typing import Optional

from langchain.chat_models.base import BaseChatModel
from langchain.memory import ConversationBufferMemory
from pydantic import ValidationError

from apps.chat.conversation import AgentConversation, AssistantConversation, BasicConversation, Conversation
from apps.chat.exceptions import ChatException
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SafetyLayer


def create_conversation(
    prompt_str: str,
    source_material: str,
    llm: BaseChatModel,
    experiment_session: Optional[ExperimentSession] = None,
) -> Conversation:
    try:
        if experiment_session and experiment_session.experiment.assistant:
            return AssistantConversation(experiment_session)
        if experiment_session and experiment_session.experiment.tools_enabled:
            return AgentConversation(
                prompt_str=prompt_str,
                source_material=source_material,
                memory=ConversationBufferMemory(return_messages=True),
                llm=llm,
                experiment_session=experiment_session,
            )
        else:
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
    def __init__(self, session: ExperimentSession):
        experiment = session.experiment
        self.prompt = experiment.prompt_text
        self.input_formatter = experiment.input_formatter
        self.llm = experiment.get_chat_model()
        self.source_material = experiment.source_material.material if experiment.source_material else None
        self.safety_layers = experiment.safety_layers.all()
        self.chat = session.chat
        self.session = session
        self.max_token_limit = experiment.max_token_limit

        self.input_tokens = 0
        self.output_tokens = 0

        self._initialize()

    def _initialize(self):
        self.conversation = create_conversation(
            self.prompt, self.source_material, self.llm, experiment_session=self.session
        )

        # load up the safety bots. They should not be agents. We don't want them using tools (for now)
        self.safety_bots = [
            SafetyBot(safety_layer, self.llm, self.source_material) for safety_layer in self.safety_layers
        ]

        self.conversation.load_memory_from_chat(self.chat, self.max_token_limit)

    def _call_predict(self, input_str):
        response, prompt_tokens, completion_tokens = self.conversation.predict(input=input_str)
        self.input_tokens = self.input_tokens + prompt_tokens
        self.output_tokens = self.output_tokens + completion_tokens
        return response

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

    def process_input(self, user_input: str, save_input_to_history=True):
        if save_input_to_history:
            self._save_message_to_history(user_input, ChatMessageType.HUMAN)
        response = self._get_response(user_input)
        self._save_message_to_history(response, ChatMessageType.AI)
        return response

    def _get_response(self, input_str: str):
        # human safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_human_messages() and not safety_bot.is_safe(input_str):
                notify_users_of_violation(self.session.id, safety_layer_id=safety_bot.safety_layer.id)
                return self._get_safe_response(safety_bot.safety_layer)

        # if we made it here there weren't any relevant human safety issues
        if self.input_formatter:
            input_str = self.input_formatter.format(input=input_str)
        response = self._call_predict(input_str)

        # ai safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_ai_messages() and not safety_bot.is_safe(response):
                return self._get_safe_response(safety_bot.safety_layer)

        return response

    def _get_safe_response(self, safety_layer: SafetyLayer):
        no_answer = "Sorry, I can't answer that. Please try something else."
        if safety_layer.prompt_to_bot:
            print("========== safety bot response =========")
            print(f"passing input: {safety_layer.prompt_to_bot}")
            safety_response = self._call_predict(safety_layer.prompt_to_bot)
            print(f"got back: {safety_response}")
            print("========== end safety bot response =========")

        else:
            safety_response = safety_layer.default_response_to_user or no_answer
        return safety_response

    def _save_message_to_history(self, message: str, type_: ChatMessageType):
        # save messages individually to get correct timestamps
        ChatMessage.objects.create(
            chat=self.chat,
            message_type=type_.value,
            content=message,
        )


class SafetyBot:
    def __init__(self, safety_layer: SafetyLayer, llm: BaseChatModel, source_material: Optional[str]):
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
        print("========== safety bot analysis =========")
        print(f"input: {input_str}")
        result = self._call_predict(input_str)
        print(f"response: {result}")
        print("========== end safety bot analysis =========")
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

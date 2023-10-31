from typing import List, Optional

from langchain.chat_models.base import BaseLanguageModel
from langchain.memory import ConversationBufferMemory
from langchain.schema import AIMessage, HumanMessage
from pydantic import ValidationError

from apps.chat.conversation import Conversation
from apps.chat.exceptions import ChatException
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, Prompt, SafetyLayer


def create_conversation(
    prompt_str: str,
    source_material: str,
    llm: BaseLanguageModel,
    experiment_session: Optional[ExperimentSession] = None,
) -> Conversation:
    try:
        return Conversation(
            prompt_str=prompt_str,
            source_material=source_material,
            memory=ConversationBufferMemory(return_messages=True),
            llm=llm,
            experiment_session=experiment_session,
        )
    except ValidationError as e:
        raise ChatException(str(e)) from e


class TopicBot:
    def __init__(
        self,
        prompt: Prompt,
        source_material: str,
        llm: BaseLanguageModel,
        safety_layers: List[SafetyLayer] = None,
        chat=None,
        messages_history=None,
        session: Optional[ExperimentSession] = None,
    ):
        self.prompt = prompt
        self.safety_layers = safety_layers or []
        self.llm = llm
        self.source_material = source_material
        self.safe_mode = bool(self.safety_layers)
        self.chat = chat
        self.session_id = session.id if session else None
        self.input_tokens = 0
        self.output_tokens = 0
        self._initialize(messages_history)

    @classmethod
    def from_experiment_session(cls, session: ExperimentSession) -> "TopicBot":
        """Shortcut to instantiate a TopicBot using an existing ExperimentSession"""
        experiment = session.experiment
        return TopicBot(
            prompt=experiment.chatbot_prompt,
            llm=experiment.get_chat_model(),
            source_material=experiment.source_material.material if experiment.source_material else None,
            safety_layers=experiment.safety_layers.all(),
            chat=session.chat,
            session=session,
        )

    def _initialize(self, messages_history):
        self.conversation = create_conversation(
            self.prompt.prompt,
            self.source_material,
            self.llm,
            experiment_session=ExperimentSession.objects.filter(id=self.session_id).first(),
        )

        # load up the safety bots. They should not be agents. We don't want them using tools (for now)
        self.safety_bots = [
            SafetyBot(safety_layer, self.llm, self.source_material) for safety_layer in self.safety_layers
        ]

        if self.chat:
            self.conversation.memory.chat_memory.messages = self.chat.get_langchain_messages()
        elif messages_history is not None:
            # Add the history messages. This origintated for the promptbuilder
            # where we maintain state client side
            history = []
            for message in messages_history:
                if message["author"] == "User":
                    history.append(HumanMessage(content=message["message"]))
                elif message["author"] == "Assistant":
                    history.append(AIMessage(content=message["message"]))
            self.conversation.memory.chat_memory.messages = history

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
                return self._get_safe_response(safety_bot.safety_layer)

        # if we made it here there weren't any relevant human safety issues
        formatted_input = self.prompt.format(input_str)
        response = self._call_predict(formatted_input)

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
        if self.chat:
            # save messages individually to get correct timestamps
            ChatMessage.objects.create(
                chat=self.chat,
                message_type=type_.value,
                content=message,
            )


class SafetyBot:
    def __init__(self, safety_layer: SafetyLayer, llm: BaseLanguageModel, source_material: Optional[str]):
        self.safety_layer = safety_layer
        self.prompt = safety_layer.prompt
        self.llm = llm
        self.source_material = source_material
        self.input_tokens = 0
        self.output_tokens = 0
        self._initialize()

    def _initialize(self):
        self.conversation = create_conversation(self.prompt.prompt, self.source_material, self.llm)

    def _call_predict(self, input_str):
        response, prompt_tokens, completion_tokens = self.conversation.predict(input=input_str)
        self.input_tokens = self.input_tokens + prompt_tokens
        self.output_tokens = self.output_tokens + completion_tokens
        return response

    def is_safe(self, input_str: str) -> bool:
        print("========== safety bot analysis =========")
        print(f"input: {input_str}")
        result = self._call_predict(self.prompt.format(input_str))
        print(f"response: {result}")
        print("========== end safety bot analysis =========")
        if result.lower().startswith("safe"):
            return True
        elif result.lower().startswith("unsafe"):
            return False
        else:
            return False

    def filter_human_messages(self) -> bool:
        return self.safety_layer.messages_to_review == "human"

    def filter_ai_messages(self) -> bool:
        return self.safety_layer.messages_to_review == "ai"


def get_bot_from_session(session: ExperimentSession) -> TopicBot:
    return TopicBot.from_experiment_session(session)


def get_bot_from_experiment(experiment: Experiment, chat: Chat):
    session = ExperimentSession.objects.filter(experiment=experiment, chat=chat).first()
    return TopicBot(
        prompt=experiment.chatbot_prompt,
        source_material=experiment.source_material.material if experiment.source_material else None,
        llm=experiment.get_chat_model(),
        safety_layers=experiment.safety_layers.all(),
        chat=chat,
        session=session,
    )

from langchain.chat_models.base import BaseChatModel
from langchain.memory import ConversationBufferMemory
from pydantic import ValidationError

from apps.chat.conversation import BasicConversation, Conversation
from apps.chat.exceptions import ChatException
from apps.experiments.models import ExperimentSession, SafetyLayer
from apps.service_providers.llm_service.runnables import create_experiment_runnable


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
        self.chain = create_experiment_runnable(self.session.experiment, self.session)

        # load up the safety bots. They should not be agents. We don't want them using tools (for now)
        self.safety_bots = [
            SafetyBot(safety_layer, self.llm, self.source_material) for safety_layer in self.safety_layers
        ]

    def _call_predict(self, input_str, save_input_to_history=True):
        result = self.chain.invoke(
            input_str,
            config={
                "configurable": {
                    "save_input_to_history": save_input_to_history,
                }
            },
        )
        self.input_tokens = self.input_tokens + result.prompt_tokens
        self.output_tokens = self.output_tokens + result.completion_tokens
        return result.output

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
        # human safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_human_messages() and not safety_bot.is_safe(user_input):
                notify_users_of_violation(self.session.id, safety_layer_id=safety_bot.safety_layer.id)
                return self._get_safe_response(safety_bot.safety_layer)

        response = self._call_predict(user_input, save_input_to_history)

        # ai safety layers
        for safety_bot in self.safety_bots:
            if safety_bot.filter_ai_messages() and not safety_bot.is_safe(response):
                return self._get_safe_response(safety_bot.safety_layer)

        return response

    def _get_safe_response(self, safety_layer: SafetyLayer):
        if safety_layer.prompt_to_bot:
            print("========== safety bot response =========")
            print(f"passing input: {safety_layer.prompt_to_bot}")
            safety_response = self._call_predict(safety_layer.prompt_to_bot, save_input_to_history=False)
            print(f"got back: {safety_response.output}")
            print("========== end safety bot response =========")
            return safety_response.output
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

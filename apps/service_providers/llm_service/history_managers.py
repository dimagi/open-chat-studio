# This file now contains only generic/reusable LLM service history manager components.
# Experiment-specific history managers have been moved to apps/experiments/runnables.py

from abc import ABCMeta, abstractmethod


class BaseHistoryManager(metaclass=ABCMeta):
    @abstractmethod
    def get_chat_history(self, input_messages: list):
        pass

    @abstractmethod
    def add_messages_to_history(
        self,
        input: str,
        save_input_to_history: bool,
        input_message_metadata: dict,
        output: str,
        save_output_to_history: bool,
        experiment_tag: str,
        output_message_metadata: dict,
    ):
        pass


class AssistantPipelineHistoryManager(BaseHistoryManager):
    """Generic history manager for pipeline-based assistants.

    This is used by pipelines and chatbots (non-experiment systems).
    """

    def __init__(self):
        self.input_message_metadata = {}
        self.output_message_metadata = {}

    def get_chat_history(self, input_messages: list):
        raise NotImplementedError()

    def add_messages_to_history(
        self, input: str, input_message_metadata: dict, output: str, output_message_metadata: dict, *args, **kwargs
    ):
        self.input_message_metadata = input_message_metadata
        self.output_message_metadata = self.output_message_metadata | output_message_metadata

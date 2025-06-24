import factory
from factory.django import DjangoModelFactory

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageTypeChoices,
    EvaluationRun,
    Evaluator,
)
from apps.utils.factories.experiment import ChatMessageFactory
from apps.utils.factories.team import TeamFactory


class EvaluatorFactory(DjangoModelFactory):
    class Meta:
        model = Evaluator

    team = factory.SubFactory(TeamFactory)
    type = "LLM"
    params = {
        "llm_prompt": "give me the sentiment of the user messages",
        "output_schema": {"sentiment": "the sentiment"},
    }


class EvaluationMessageFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationMessage
        skip_postgeneration_save = True

    human_message_content = factory.Faker("sentence")
    ai_message_content = factory.Faker("sentence")
    context = {"current_datetime": "2023-01-01T00:00:00", "history": "test history"}

    @factory.post_generation
    def create_chat_messages(self, create, extracted, **kwargs):
        """Optionally create associated chat messages"""
        if create and extracted:
            from apps.chat.models import ChatMessageType
            from apps.utils.factories.experiment import ChatFactory

            chat = ChatFactory()
            human_chat_message = ChatMessageFactory(
                message_type=ChatMessageType.HUMAN, content=self.human_message_content, chat=chat
            )
            ai_chat_message = ChatMessageFactory(
                message_type=ChatMessageType.AI, content=self.ai_message_content, chat=chat
            )
            self.human_chat_message = human_chat_message
            self.ai_chat_message = ai_chat_message
            self.save()


class EvaluationDatasetFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationDataset
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    name = factory.Faker("name")

    @factory.post_generation
    def messages(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for message in extracted:
                self.messages.add(message)
        else:
            self.messages.add(EvaluationMessageFactory())


class EvaluationConfigFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationConfig
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    name = factory.Faker("name")
    dataset = factory.SubFactory(EvaluationDatasetFactory)
    message_type = EvaluationMessageTypeChoices.ALL

    @factory.post_generation
    def evaluators(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for evaluator in extracted:
                self.evaluators.add(evaluator)
        else:
            self.evaluators.add(EvaluatorFactory())


class EvaluationRunFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationRun

    team = factory.SubFactory(TeamFactory)
    config = factory.SubFactory(EvaluationConfigFactory)

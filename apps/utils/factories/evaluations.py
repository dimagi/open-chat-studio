import factory
from factory.django import DjangoModelFactory

from apps.annotations.models import Tag
from apps.chat.models import ChatMessageType
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationResult,
    EvaluationRun,
    Evaluator,
    EvaluatorTagRule,
)
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory
from apps.utils.factories.team import TeamFactory


class EvaluatorFactory(DjangoModelFactory):
    class Meta:
        model = Evaluator

    team = factory.SubFactory(TeamFactory)
    type = "LlmEvaluator"
    name = factory.Sequence(lambda n: f"Test Evaluator {n}")
    evaluation_mode = "message"
    params = factory.LazyFunction(
        lambda: {
            "llm_prompt": "give me the sentiment of the user messages",
            "output_schema": {
                "sentiment": {
                    "type": "choice",
                    "description": "the sentiment",
                    "choices": ["positive", "neutral", "negative"],
                    "use_in_aggregations": True,
                },
                "score": {
                    "type": "int",
                    "description": "score from 1 to 10",
                    "ge": 1,
                    "le": 10,
                    "use_in_aggregations": True,
                },
            },
        }
    )


class EvaluationTagFactory(DjangoModelFactory):
    """Factory for plain user tags used by evaluator tag rules."""

    class Meta:
        model = Tag
        django_get_or_create = ("team", "name", "is_system_tag", "category")

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"eval-tag-{n}")
    is_system_tag = False
    category = ""


class EvaluatorTagRuleFactory(DjangoModelFactory):
    class Meta:
        model = EvaluatorTagRule

    team = factory.SubFactory(TeamFactory)
    evaluator = factory.SubFactory(EvaluatorFactory, team=factory.SelfAttribute("..team"))
    tag = factory.SubFactory(EvaluationTagFactory, team=factory.SelfAttribute("..team"))
    field_name = "sentiment"
    condition_type = ConditionType.EQUALS
    condition_value = factory.LazyAttribute(lambda _: {"value": "negative"})


class AppliedTagFactory(DjangoModelFactory):
    class Meta:
        model = AppliedTag

    team = factory.SubFactory(TeamFactory)
    rule = factory.SubFactory(EvaluatorTagRuleFactory, team=factory.SelfAttribute("..team"))
    tag = factory.LazyAttribute(lambda obj: obj.rule.tag)
    evaluation_result = factory.SubFactory(
        "apps.utils.factories.evaluations.EvaluationResultFactory",
        team=factory.SelfAttribute("..team"),
        evaluator=factory.SelfAttribute("..rule.evaluator"),
    )


class EvaluationMessageFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationMessage
        skip_postgeneration_save = True

    input = {"content": "Hello, how are you?", "role": "human"}
    output = {"content": "I'm doing well, thank you!", "role": "ai"}
    context = {"current_datetime": "2023-01-01T00:00:00"}

    @factory.post_generation
    def create_chat_messages(self, create, extracted, **kwargs):
        """Optionally create associated chat messages"""
        if create and extracted:
            chat = ChatFactory.create()
            if self.input:
                input_chat_message = ChatMessageFactory.create(
                    message_type=ChatMessageType.HUMAN, content=self.input["content"], chat=chat
                )
                self.input_chat_message = input_chat_message
            if self.output:
                output_chat_message = ChatMessageFactory.create(
                    message_type=ChatMessageType.AI, content=self.output["content"], chat=chat
                )
                self.expected_output_chat_message = output_chat_message
            self.save()


class EvaluationDatasetFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationDataset
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Dataset {n}")
    evaluation_mode = "message"

    @factory.post_generation
    def messages(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for message in extracted:
                self.messages.add(message)
        else:
            self.messages.add(EvaluationMessageFactory.create())


class EvaluationConfigFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationConfig
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Test Eval Config {n}")
    dataset = factory.SubFactory(EvaluationDatasetFactory)

    @factory.post_generation
    def evaluators(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted:
            for evaluator in extracted:
                self.evaluators.add(evaluator)
        else:
            self.evaluators.add(EvaluatorFactory.create())


class EvaluationRunFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationRun

    team = factory.SubFactory(TeamFactory)
    config = factory.SubFactory(EvaluationConfigFactory)


class EvaluationResultFactory(DjangoModelFactory):
    class Meta:
        model = EvaluationResult

    team = factory.SubFactory(TeamFactory)
    evaluator = factory.SubFactory(EvaluatorFactory)
    message = factory.SubFactory(EvaluationMessageFactory)
    run = factory.SubFactory(EvaluationRunFactory)
    output = {}

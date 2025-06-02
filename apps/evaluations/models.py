from collections import defaultdict
from collections.abc import Iterable
from typing import cast

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import BaseMessage, messages_from_dict

from apps.chat.models import ChatMessage
from apps.evaluations import evaluators
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Evaluator(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = models.JSONField(
        default=dict
    )  # This is different for each evaluator. Usage is similar to how we define Nodes in pipelines

    def __str__(self):
        return f"{self.name} ({self.type})"

    def run(self, message: BaseMessage) -> evaluators.EvaluatorResult:
        try:
            evaluator = getattr(evaluators, self.type)
            return evaluator(**self.params).run(message)
        except:
            raise  # TODO

    def get_absolute_url(self):
        return reverse("evaluations:evaluator_edit", args=[self.team.slug, self.id])


class EvaluationMessageType(models.TextChoices):
    HUMAN = "human", "Human"
    AI = "ai", "AI"


class EvaluationMessage(BaseModel):
    chat_message = models.ForeignKey(ChatMessage, on_delete=models.SET_NULL, null=True, blank=True)
    # This is null when it is generated manually

    message_type = models.CharField(max_length=10, choices=EvaluationMessageType.choices)
    content = models.TextField()
    metadata = models.JSONField(default=dict)

    def __str__(self):
        return f"{self.get_message_type_display()}: {self.content}"

    @staticmethod
    def from_chat_message(chat_message: ChatMessage) -> "EvaluationMessage":
        return EvaluationMessage.objects.create(
            chat_message=chat_message,
            message_type=chat_message.message_type,
            content=chat_message.content,
            metadata=chat_message.metadata,
        )

    def to_langchain_message(self) -> BaseMessage:
        return messages_from_dict(
            [
                {
                    "type": self.message_type,
                    "data": {
                        "content": self.content,
                        "additional_kwargs": {
                            "id": self.id,
                        },
                    },
                }
            ]
        )[0]


class DatasetMessageTypeChoices(models.TextChoices):
    HUMAN = "human", "Human Only"
    AI = "ai", "AI Only"
    ALL = "all", "All"


class EvaluationDataset(BaseTeamModel):
    message_type = models.CharField(max_length=10, choices=DatasetMessageTypeChoices)

    name = models.CharField(max_length=255)
    messages = models.ManyToManyField(EvaluationMessage)

    def __str__(self):
        return f"{self.name} ({self.messages.count()} messages)"

    def iter_messages(self):
        if self.message_type == DatasetMessageTypeChoices.ALL:
            return self.messages.all()

        return self.messages.filter(message_type=self.message_type)


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    # experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[self.team.slug, self.id])

    def run(self) -> "EvaluationRun":
        # TODO: Run this in a celery task
        """Runs the evaluation"""
        run = EvaluationRun.objects.create(team=self.team, config=self)
        results = []
        # TODO: Run in parallel with langgraph
        for evaluator in cast(Iterable[Evaluator], self.evaluators.all()):
            for message in self.dataset.messages.all():
                result = evaluator.run(message.to_langchain_message())
                results.append(
                    EvaluationResult.objects.create(
                        message=message, run=run, evaluator=evaluator, output=result.model_dump(), team=self.team
                    )
                )
        run.finished_at = timezone.now()
        run.save()
        return run


class EvaluationRun(BaseTeamModel):
    config = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )  # if manually triggered, who did it

    def __str__(self):
        return f"EvaluationRun ({self.created_at} - {self.finished_at})"

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_results_home", args=[self.team.slug, self.config_id, self.pk])

    def get_table_data(self):
        results = self.results.all()
        table_by_message = defaultdict(dict)
        for result in results:
            table_by_message[result.message.id].update(
                {
                    "message": result.message.content,
                    **{
                        f"{key} ({result.evaluator.name})": value
                        for key, value in result.output.get("result", {}).items()
                    },
                }
            )
        return table_by_message.values()


class EvaluationResult(BaseTeamModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    message = models.ForeignKey(EvaluationMessage, on_delete=models.CASCADE)
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    output = models.JSONField()
    # TODO: track input with a generic FK relationship / normalized inputs

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

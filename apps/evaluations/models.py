from __future__ import annotations

import importlib
from collections import defaultdict
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.urls import reverse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel as PydanticBaseModel

from apps.chat.models import ChatMessage
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel

if TYPE_CHECKING:
    from apps.evaluations.evaluators import EvaluatorResult


class Evaluator(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = models.JSONField(
        default=dict
    )  # This is different for each evaluator. Usage is similar to how we define Nodes in pipelines

    def __str__(self):
        return f"{self.name} ({self.type})"

    @cached_property
    def evaluator(self):
        module = importlib.import_module("apps.evaluations.evaluators")
        return getattr(module, self.type)

    def run(self, message: EvaluationMessage, message_type: EvaluationMessageTypeChoices) -> EvaluatorResult:
        try:
            return self.evaluator(**self.params).run(message, message_type)
        except:
            raise  # TODO

    def get_absolute_url(self):
        return reverse("evaluations:evaluator_edit", args=[self.team.slug, self.id])


class EvaluationMessageContext(PydanticBaseModel):
    current_datetime: datetime
    history: list[BaseMessage]


class EvaluationMessage(BaseModel):
    human_chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="human_evaluation_messages"
    )
    # null when it is generated manually
    ai_chat_message = models.ForeignKey(
        ChatMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_evaluation_messages"
    )
    # null when it is generated manually

    human_message_content = models.TextField()
    ai_message_content = models.TextField()
    context = models.JSONField(default=dict)

    metadata = models.JSONField(default=dict)

    def __str__(self):
        return f"Human: {self.human_message_content}, AI: {self.ai_message_content}"

    def as_langchain_messages(self) -> list[BaseMessage]:
        """
        Converts this message instance into a list of Langchain `BaseMessage` objects.
        """
        return [
            self.as_human_langchain_message(),
            self.as_ai_langchain_message(),
        ]

    def as_human_langchain_message(self) -> BaseMessage:
        return HumanMessage(
            content=self.human_message_content,
            additional_kwargs={"id": self.id, "chat_message_id": self.human_chat_message_id},
        )

    def as_ai_langchain_message(self) -> BaseMessage:
        return AIMessage(
            content=self.ai_message_content,
            additional_kwargs={"id": self.id, "chat_message_id": self.ai_chat_message_id},
        )


class EvaluationMessageTypeChoices(models.TextChoices):
    HUMAN = "human", "Human Only"
    AI = "ai", "AI Only"
    ALL = "all", "All"


class EvaluationDataset(BaseTeamModel):
    name = models.CharField(max_length=255)
    messages = models.ManyToManyField(EvaluationMessage)

    def __str__(self):
        return f"{self.name} ({self.messages.count()} messages)"

    def get_absolute_url(self):
        return reverse("evaluations:dataset_edit", args=[self.team.slug, self.id])


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    message_type = models.CharField(
        max_length=10,
        choices=EvaluationMessageTypeChoices,
        default=EvaluationMessageTypeChoices.ALL,
    )

    # experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[self.team.slug, self.id])

    def run(self) -> EvaluationRun:
        """Runs the evaluation asynchronously using Celery"""
        run = EvaluationRun.objects.create(team=self.team, config=self, status=EvaluationRunStatus.PENDING)

        from apps.evaluations.tasks import run_evaluation_task

        result = run_evaluation_task.delay(run.id)
        run.job_id = result.id
        run.save(update_fields=["job_id"])

        return run


class EvaluationRunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class EvaluationRun(BaseTeamModel):
    config = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )  # if manually triggered, who did it
    status = models.CharField(max_length=20, choices=EvaluationRunStatus.choices, default=EvaluationRunStatus.PENDING)
    job_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)

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
                    "human_message": result.message.human_message_content,
                    "ai_message": result.message.ai_message_content,
                    **{f"{key}": value for key, value in result.message.context.items()},
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

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

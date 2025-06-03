from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator, Iterable
from datetime import datetime
from typing import cast

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel as PydanticBaseModel

from apps.chat.models import ChatMessage, ChatMessageType
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

    def run(self, messages: list[BaseMessage]) -> evaluators.EvaluatorResult:
        try:
            evaluator = getattr(evaluators, self.type)
            return evaluator(**self.params).run(messages)
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

    @staticmethod
    def from_chat_messages(human_chat_message: ChatMessage, ai_chat_message: ChatMessage) -> EvaluationMessage:
        if (
            human_chat_message.message_type != ChatMessageType.HUMAN
            or ai_chat_message.message_type != ChatMessageType.AI
        ):
            raise ValueError(
                f"Expected HUMAN and AI types, got {human_chat_message.message_type} and {ai_chat_message.message_type}"
            )
        if human_chat_message.chat_id != ai_chat_message.chat_id:
            raise ValueError("Messages are from different chats")
        if ai_chat_message.created_at <= human_chat_message.created_at:
            raise ValueError("AI message must be created after the human message")

        return EvaluationMessage.objects.create(
            human_chat_message=human_chat_message,
            human_message_content=human_chat_message.content,
            ai_chat_message=ai_chat_message,
            ai_message_content=ai_chat_message.content,
            context={
                "current_datetime": human_chat_message.created_at,
                "history": "\n".join(
                    f"{message.message_type}: {message.content}"
                    for message in human_chat_message.chat.get_langchain_messages()
                ),
            },
        )

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


class DatasetMessageTypeChoices(models.TextChoices):
    HUMAN = "human", "Human Only"
    AI = "ai", "AI Only"
    ALL = "all", "All"


class EvaluationDataset(BaseTeamModel):
    name = models.CharField(max_length=255)
    messages = models.ManyToManyField(EvaluationMessage)

    def __str__(self):
        return f"{self.name} ({self.messages.count()} messages)"


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    message_type = models.CharField(
        max_length=10,
        choices=DatasetMessageTypeChoices,
        default=DatasetMessageTypeChoices.ALL,
    )

    # experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def get_absolute_url(self):
        return reverse("evaluations:evaluation_runs_home", args=[self.team.slug, self.id])

    def iter_messages(self) -> Generator[tuple[int, list[BaseMessage]], None, None]:
        for message in self.dataset.messages.all():
            if self.message_type == DatasetMessageTypeChoices.ALL:
                yield (message.id, message.as_langchain_messages())
            elif self.message_type == DatasetMessageTypeChoices.HUMAN:
                yield (message.id, [message.as_human_langchain_message()])
            elif self.message_type == DatasetMessageTypeChoices.AI:
                yield (message.id, [message.as_ai_langchain_message()])

    def run(self) -> EvaluationRun:
        # TODO: Run this in a celery task
        """Runs the evaluation"""
        run = EvaluationRun.objects.create(team=self.team, config=self)
        results = []
        # TODO: Run in parallel with langgraph
        for evaluator in cast(Iterable[Evaluator], self.evaluators.all()):
            for message_id, messages in self.iter_messages():
                result = evaluator.run(messages)
                results.append(
                    EvaluationResult.objects.create(
                        message_id=message_id, run=run, evaluator=evaluator, output=result.model_dump(), team=self.team
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
    # TODO: track input with a generic FK relationship / normalized inputs

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

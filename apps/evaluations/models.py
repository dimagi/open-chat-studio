from collections.abc import Iterable
from typing import cast

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from langchain_core.messages import BaseMessage

from apps.evaluations import evaluators
from apps.experiments.models import ExperimentSession
from apps.teams.models import BaseTeamModel


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


class EvaluationDataset(BaseTeamModel):
    MESSAGE_TYPE_CHOICES = [
        ("USER_ONLY", "User Only"),
        ("BOT_ONLY", "Bot Only"),
        ("ALL", "All"),
    ]
    message_type = models.CharField(max_length=32, choices=MESSAGE_TYPE_CHOICES)

    name = models.CharField(max_length=255)
    # version = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # If this is null, this should target the latest working version.
    sessions = models.ManyToManyField(ExperimentSession)

    def __str__(self):
        return f"EvaluationDataset ({self.version.version_number if self.version else 'Working'})"


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    # experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig ({self.name})"

    def get_absolute_url(self):
        return reverse("evaluations:runs_table", args=[self.team.slug, self.id])

    def run(self) -> list["EvaluationResult"]:
        # TODO: Run this in a celery task
        """Runs the evaluation"""
        run = EvaluationRun.objects.create(team=self.team, config=self)
        results = []
        # TODO: Run in parallel with langgraph
        for evaluator in cast(Iterable[Evaluator], self.evaluators.all()):
            # TODO: Filter sessions
            for session in self.dataset.sessions.all():
                # TODO: pass in the correct messages based on dataset.message_types
                result = evaluator.run(session.chat.get_langchain_messages())
                results.append(
                    EvaluationResult.objects.create(
                        session=session, run=run, evaluator=evaluator, output=result.model_dump(), team=self.team
                    )
                )
        run.finished_at = timezone.now()
        run.save()
        return results


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
        return reverse("evaluations:run_detail", args=[self.team.slug, self.config_id, self.pk])


class EvaluationResult(BaseTeamModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    session = models.ForeignKey(ExperimentSession, null=True, on_delete=models.SET_NULL)
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    output = models.JSONField()
    # TODO: track input with a generic FK relationship / normalized inputs

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

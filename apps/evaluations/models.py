from collections.abc import Iterable
from typing import cast

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.evaluations import evaluators
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import BaseTeamModel


class Evaluator(BaseTeamModel):
    type = models.CharField(max_length=128)  # The evaluator type, should be one from evaluators.py
    params = models.JSONField(
        default=dict
    )  # This is different for each evaluator. Usage is similar to how we define Nodes in pipelines

    def __str__(self):
        return f"Evaluator ({self.type})"

    def run(self, dataset: "EvaluationDataset") -> evaluators.EvaluatorResult:
        try:
            evaluator = getattr(evaluators, self.type)
            return evaluator(**self.params).run(dataset.get_messages())
        except:
            raise  # TODO


class EvaluationDataset(BaseTeamModel):
    MESSAGE_TYPE_CHOICES = [
        ("USER_ONLY", "User Only"),
        ("BOT_ONLY", "Bot Only"),
        ("ALL", "All"),
    ]
    message_type = models.CharField(max_length=32, choices=MESSAGE_TYPE_CHOICES)
    version = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # If this is null, this should target the latest working version.
    sessions = models.ManyToManyField(ExperimentSession)

    def __str__(self):
        return f"EvaluationDataset ({self.version.version_number if self.version else 'Working'})"

    def get_messages(self):
        # TODO: use self.message_type to filter messages
        messages = []
        for session in self.sessions.all():
            messages.extend(session.chat.get_langchain_messages())
        return messages


class EvaluationConfig(BaseTeamModel):
    name = models.CharField(max_length=255)
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig (experiment={self.experiment_id})"

    def run(self) -> list["EvaluationResult"]:
        """Runs the evaluation"""
        run = EvaluationRun.objects.create(team=self.team, config=self)
        results = []
        for evaluator in cast(Iterable[Evaluator], self.evaluators.all()):
            result = evaluator.run(self.dataset)
            eval_result = EvaluationResult.objects.create(
                run=run, evaluator=evaluator, output=result.model_dump_json(), team=self.team
            )
            results.append(eval_result)
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


class EvaluationResult(BaseTeamModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    output = models.JSONField()
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    # TODO: track input with a generic FK relationship / normalized inputs

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

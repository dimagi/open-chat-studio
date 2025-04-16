from django.conf import settings
from django.db import models

from apps.experiments.models import Experiment, ExperimentSession


class Evaluator(models.Model):
    TYPE_CHOICES = [
        ("LLM", "LLM"),
    ]
    type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    params = models.JSONField()  # This is different for each evaluator. TODO: enforce schemas

    def __str__(self):
        return f"Evaluator ({self.type})"


class EvaluationDataset(models.Model):
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


class EvaluationConfig(models.Model):
    evaluators = models.ManyToManyField(Evaluator)
    dataset = models.ForeignKey(EvaluationDataset, on_delete=models.CASCADE)
    experiment = models.ForeignKey(Experiment, on_delete=models.SET_NULL, null=True, blank=True)
    # The bot / experiment we are targeting

    def __str__(self):
        return f"EvaluationConfig (experiment={self.experiment_id})"


class EvaluationRun(models.Model):
    config = models.ForeignKey(EvaluationConfig, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )  # if manually triggered, who did it

    def __str__(self):
        return f"EvaluationRun ({self.created_at} - {self.finished_at})"


class EvaluatorResult(models.Model):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE)
    output = models.JSONField()
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    # TODO: track input with a generic FK relationship / normalized inputs

    def __str__(self):
        return f"EvaluatorResult for Evaluator {self.evaluator_id}"

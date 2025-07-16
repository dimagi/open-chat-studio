from django.db import models

from apps.utils.models import BaseModel


class Trace(BaseModel):
    experiment = models.ForeignKey(
        "experiments.Experiment", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    session = models.ForeignKey(
        "experiments.ExperimentSession", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    participant = models.ForeignKey(
        "experiments.Participant", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    input_message_id = models.CharField(max_length=255, blank=True)
    output_message_id = models.CharField(max_length=255, blank=True)
    duration = models.IntegerField()

    def __str__(self):
        return f"Trace {self.experiment} {self.session} {self.duration}ms"

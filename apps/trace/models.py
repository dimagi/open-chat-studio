from django.db import models


class Trace(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    experiment = models.ForeignKey(
        "experiments.Experiment", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    session = models.ForeignKey(
        "experiments.ExperimentSession", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    participant = models.ForeignKey(
        "experiments.Participant", on_delete=models.SET_NULL, null=True, related_name="traces"
    )
    input_message = models.ForeignKey(
        "chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="input_message_trace"
    )
    output_message = models.ForeignKey(
        "chat.ChatMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="output_message_trace"
    )
    team = models.ForeignKey("teams.team", on_delete=models.SET_NULL, null=True, related_name="traces")
    duration = models.IntegerField()

    def __str__(self):
        return f"Trace {self.experiment} {self.session} {self.duration}ms"

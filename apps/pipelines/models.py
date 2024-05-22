from django.db import models
from langchain_core.runnables import RunnableConfig

from apps.pipelines.logging import PipelineLoggingCallbackHandler
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Pipeline(BaseTeamModel):
    name = models.CharField(max_length=128)
    data = models.JSONField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def invoke(self, input):
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_json(self.data)
        return runnable.invoke(input, config=RunnableConfig(callbacks=[PipelineLoggingCallbackHandler(self)]))


class PipelineRun(BaseModel):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField()
    log = models.TextField(blank=True)

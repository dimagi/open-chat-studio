import logging
from contextlib import contextmanager

from celery import shared_task
from django.utils import timezone

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.log import LogEntry, Logger, LogLevel, LogStream
from apps.analysis.models import AnalysisRun, RunStatus
from apps.analysis.pipelines import get_source_pipeline
from apps.analysis.serializers import create_resource_for_data

log = logging.getLogger(__name__)


@contextmanager
def run_context(run):
    run.start_time = timezone.now()
    run.status = RunStatus.RUNNING
    run.save()

    try:
        yield
        run.status = RunStatus.SUCCESS
    except Exception as e:
        run.status = RunStatus.ERROR
        run.error = repr(e)
        log.exception("Error running analysis")
    finally:
        run.end_time = timezone.now()
        run.save()


class RunLogStream(LogStream):
    def __init__(self, run, level: LogLevel = LogLevel.INFO):
        self.run = run
        self.level = level
        self.logs = []

    def write(self, entry: LogEntry):
        if entry.level >= LogLevel.INFO:
            self.logs.append(entry.to_json())
            self.flush()

    def flush(self):
        self.run.log = {"entries": self.logs}
        self.run.save(update_fields=["log"])


@shared_task
def run_pipeline(run_id: int):
    run = AnalysisRun.objects.get(id=run_id)
    log_stream = RunLogStream(run)
    llm_service = run.analysis.llm_provider.get_llm_service()
    with run_context(run):
        pipline_context = PipelineContext(llm_service, logger=Logger(log_stream), params=run.params)
        pipeline = get_source_pipeline(run.analysis.source)
        result = pipeline.run(pipline_context, StepContext.initial())
        resource = create_resource_for_data(run.team, result.data)
        run.resources.add(resource)

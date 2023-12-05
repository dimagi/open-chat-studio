import logging
import time
from contextlib import contextmanager

from celery import shared_task
from django.utils import timezone

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.log import LogEntry, Logger, LogLevel, LogStream
from apps.analysis.models import AnalysisRun, RunGroup, RunStatus
from apps.analysis.pipelines import get_data_pipeline, get_source_pipeline
from apps.analysis.serializers import create_resource_for_data, get_serializer

log = logging.getLogger(__name__)


@contextmanager
def run_status_context(run, raise_errors=False):
    run.start_time = timezone.now()
    run.status = RunStatus.RUNNING
    run.save()

    try:
        yield
        run.status = RunStatus.SUCCESS
    except Exception as e:
        run.status = RunStatus.ERROR
        run.error = repr(e)
        if raise_errors:
            raise
        else:
            log.exception("Error running analysis")
    finally:
        run.end_time = timezone.now()
        run.save()


@contextmanager
def run_context(run):
    log_stream = RunLogStream(run)
    llm_service = run.group.analysis.llm_provider.get_llm_service()
    params = run.group.params
    params["llm_model"] = run.group.analysis.llm_model
    pipeline_context = PipelineContext(llm_service, logger=Logger(log_stream), params=params)

    with run_status_context(run, raise_errors=True):
        yield pipeline_context


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
def run_pipeline(run_group_id: int):
    group = RunGroup.objects.get(id=run_group_id)

    with run_status_context(group):
        source_run = AnalysisRun.objects.create(group=group)
        with run_context(source_run) as pipeline_context:
            source_pipeline = get_source_pipeline(group.analysis.source)
            source_result = _run_pipeline(source_run, source_pipeline, pipeline_context, StepContext.initial())

        if source_pipeline.steps[-1].output_multiple and isinstance(source_result.data, list):
            result_data = source_result.data
        else:
            result_data = [source_result.data]

        for data in result_data:
            time.sleep(5)
            run = AnalysisRun.objects.create(group=group)
            with run_context(run) as pipeline_context:
                data_pipeline = get_data_pipeline(group.analysis.pipeline)
                _run_pipeline(run, data_pipeline, pipeline_context, StepContext.initial(data))


def _run_pipeline(run, pipeline, pipeline_context: PipelineContext, input_context: StepContext) -> StepContext:
    result = pipeline.run(pipeline_context, input_context)
    if result.metadata.get("persist_output", True):
        resource = create_resource_for_data(run.group.team, result.data, f"{result.name} Output")
        run.resources.add(resource)
    run.output_summary = get_serializer(result.data).get_summary(result.data)
    return result

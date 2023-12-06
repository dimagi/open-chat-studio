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
    """Context manager to simplify updating status and start / end times as well as error handling."""
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
    """Context manager to create the pipeline context and manage run status."""
    log_stream = RunLogStream(run)
    llm_service = run.group.analysis.llm_provider.get_llm_service()
    params = run.group.params
    params["llm_model"] = run.group.analysis.llm_model
    pipeline_context = PipelineContext(llm_service, logger=Logger(log_stream), params=params)

    with run_status_context(run, raise_errors=True):
        yield pipeline_context


class RunLogStream(LogStream):
    def __init__(self, run):
        self.run = run
        self.logs = []

    def write(self, entry: LogEntry):
        self.logs.append(entry.to_json())
        self.flush()

    def flush(self):
        self.run.log = {"entries": self.logs}
        self.run.save(update_fields=["log"])


@shared_task
def run_analysis(run_group_id: int):
    group = RunGroup.objects.get(id=run_group_id)

    with run_status_context(group):
        source_result = run_pipeline(group, group.analysis.source, get_source_pipeline)

        if source_result.metadata.get("output_multiple", False) and isinstance(source_result.data, list):
            result_data = source_result.data
        else:
            result_data = [source_result.data]

        for data in result_data:
            run_pipeline(group, group.analysis.pipeline, get_data_pipeline, data=data)


def run_pipeline(group: RunGroup, pipeline_id: str, pipeline_factory, data=None) -> StepContext:
    run = AnalysisRun.objects.create(group=group)
    with run_context(run) as pipeline_context:
        pipeline = pipeline_factory(pipeline_id)
        result = pipeline.run(pipeline_context, StepContext.initial(data))
        process_pipeline_output(run, result)
        return result


def process_pipeline_output(run, result):
    if result.metadata.get("output_multiple", False) and isinstance(result.data, list):
        result_data = result.data
        result.output_summary = f"{len(result_data)} chunks created"
    else:
        result_data = [result.data]
        run.output_summary = get_serializer(result.data).get_summary(result.data)

    if result.metadata.get("persist_output", True):
        for i, data in enumerate(result_data):
            resource = create_resource_for_data(run.group.team, data, f"{result.name} Output {i}")
            run.resources.add(resource)

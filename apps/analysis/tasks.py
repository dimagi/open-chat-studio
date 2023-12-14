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
    params = run.group.params
    params["llm_model"] = run.group.analysis.llm_model
    pipeline_context = PipelineContext(run, log=Logger(log_stream), params=params, create_resources=True)

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
    group = RunGroup.objects.select_related("team", "analysis", "analysis__llm_provider").get(id=run_group_id)

    with run_status_context(group):
        source_result = run_pipeline(group, group.analysis.source, get_source_pipeline)

        if source_result.should_split:
            results = [source_result.clone_with(data) for data in source_result.data]
        else:
            results = [source_result]

        for result in results:
            run_pipeline(group, group.analysis.pipeline, get_data_pipeline, context=result)


def run_pipeline(group: RunGroup, pipeline_id: str, pipeline_factory, context=None) -> StepContext:
    run = AnalysisRun.objects.create(group=group)
    with run_context(run) as pipeline_context:
        pipeline = pipeline_factory(pipeline_id)
        result = pipeline.run(pipeline_context, context or StepContext.initial())
        process_pipeline_output(run, result)
        return result


def process_pipeline_output(run, result: StepContext):
    if result.should_split:
        run.output_summary = f"{len(result.data)} groups created"
    else:
        run.output_summary = get_serializer(result.data).get_summary(result.data)

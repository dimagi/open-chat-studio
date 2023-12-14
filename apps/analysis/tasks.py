import logging
from contextlib import contextmanager

from celery import shared_task
from django.utils import timezone

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.log import LogEntry, Logger, LogStream
from apps.analysis.models import AnalysisRun, RunGroup, RunStatus
from apps.analysis.pipelines import get_data_pipeline, get_source_pipeline
from apps.analysis.serializers import get_serializer_by_type

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
        import traceback

        traceback.print_exc()
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
        source_result = run_serial_pipeline(group, group.analysis.source, get_source_pipeline, StepContext.initial())

        if isinstance(source_result, list) and len(source_result) > 1:
            run_parallel_pipeline(group, source_result)
        else:
            next_intput = source_result[0] if isinstance(source_result, list) else source_result
            run_serial_pipeline(group, group.analysis.pipeline, get_data_pipeline, next_intput)


def run_parallel_pipeline(group, contexts):
    for context in contexts:
        assert context.resource, "Parallel pipeline requires resource to be created by source pipeline"
        run = AnalysisRun.objects.create(name=context.name, group=group, input_resource=context.resource)
        task = run_pipline_split.delay(run.id)
        run.task_id = task.task_id
        run.save()


@shared_task
def run_pipline_split(run_id: int):
    run = AnalysisRun.objects.select_related(
        "group", "group__team", "group__analysis", "group__analysis__llm_provider"
    ).get(id=run_id)
    resource = run.input_resource
    step_context = StepContext.initial(resource=resource, name=run.name)
    run_pipeline(run, run.group.analysis.pipeline, get_data_pipeline, step_context)


def run_serial_pipeline(
    group: RunGroup, pipeline_id: str, pipeline_factory, context
) -> StepContext | list[StepContext]:
    run = AnalysisRun.objects.create(name=context.name, group=group)
    return run_pipeline(run, pipeline_id, pipeline_factory, context)


def run_pipeline(run: AnalysisRun, pipeline_id: str, pipeline_factory, context) -> StepContext | list[StepContext]:
    with run_context(run) as pipeline_context:
        pipeline = pipeline_factory(pipeline_id)
        result = pipeline.run(pipeline_context, context)
        process_pipeline_output(pipeline_context, result)
        return result


def process_pipeline_output(pipeline_context: PipelineContext, result: StepContext):
    run = pipeline_context.run
    if isinstance(result, list):
        run.output_summary = f"{len(result)} groups created"
        for res in result:
            run.output_summary += f"\n  - {res.name}"
    else:
        run.output_summary = get_serializer_by_type(result.data).get_summary(result.data)

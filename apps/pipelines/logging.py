from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger
from sentry_sdk.integrations.logging import ignore_logger

from apps.pipelines.models import LogEntry, Pipeline, PipelineRunStatus


class PipelineLoggingCallbackHandler(BaseCallbackHandler):
    def __init__(self, pipeline: Pipeline, pipeline_run, logger) -> None:
        # logging handler
        # self.logging_handler = logging_handler
        # does langgraph give us a good way of getting the current step?

        self.pipeline = pipeline
        self.pipeline_run = pipeline_run
        self.depth = 0
        self.logger = logger

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        indent = "  " * self.depth
        node_id = kwargs["name"]
        # node_id__graph:step:N__uuid
        handler_id = f"{node_id}__{kwargs['tags'][0]}__{kwargs['parent_run_id']}"
        # self.logger = get_logger(handler_id, self.pipeline_run.id)
        # self.logger.info("testing")
        # self.log = f"{self.log}\n{indent}{name} inputs: {inputs}"
        self.depth += 1
        return super().on_chain_start(serialized, inputs, *args, **kwargs)

    def on_chain_end(self, outputs, **kwargs):
        self.depth -= 1
        # self.logger.info("testing")
        if self.depth == 0:
            if not self.pipeline_run.status == PipelineRunStatus.ERROR:
                self.pipeline_run.status = PipelineRunStatus.SUCCESS
            # self.pipeline_run.save()

    def on_chain_error(self, error, *args, **kwargs):
        self.pipeline_run.status = PipelineRunStatus.ERROR
        print(f"{self.log} --/--> error: {error}")
        self.log = f"{self.log} --/--> error: {error}"


class LogHandler:
    def __init__(self, pipeline_run):
        self.pipeline_run = pipeline_run

    def __call__(self, message):
        record = message.record
        log_entry = LogEntry(
            time=record["time"].strftime("%Y-%m-%d %H:%M:%S.%f"),
            level=record["level"].name,
            message=record["message"],
            # "file": record["file"].name,
            # "line": record["line"]
        )
        self.pipeline_run.append_log(log_entry)
        # try:
        #     pipeline_run = PipelineRun.objects.get(id=self.pipeline_run_id)
        #     pipeline_run.append_log(log_entry)
        # except PipelineRun.DoesNotExist:
        #     breakpoint()
        # pipeline_run.save()


def get_logger(name, pipeline_run):
    log = logger.bind(name=name)
    log.level("DEBUG")
    log.remove()
    log.add(LogHandler(pipeline_run))
    return log


def _create_logger(name, unique_id):
    log_name = f"{name}_{unique_id}"
    log = logging.getLogger(log_name)
    log.propagate = False
    log.setLevel(logging.DEBUG)
    ignore_logger(log_name)
    return log

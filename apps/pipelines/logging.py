import uuid

from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger


class PipelineLoggingCallbackHandler(BaseCallbackHandler):
    """Handles logging within the pipeline

    Since Langchain / Langgraph runs in separate threads, we append to a list
    in a thread-safe manner, then periodically write to the DB later.

    """

    def __init__(self, pipeline_run, verbose=False) -> None:
        self.pipeline_run = pipeline_run
        self.logger = get_logger(uuid.uuid4().hex, self.pipeline_run)
        self.depth = 0
        self.verbose = verbose
        self._run_id_names = {}

    def _should_log(self, kwargs):
        if "langsmith:hidden" in kwargs.get("tags", []):
            return False
        if self.verbose:
            return True
        if kwargs.get("run_id") in self._run_id_names:
            return True
        from apps.pipelines.nodes import nodes

        if hasattr(nodes, kwargs.get("name", "")):
            self._run_id_names[kwargs.get("run_id")] = kwargs.get("name")
            return True
        return False

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        self.depth += 1
        if self._should_log(kwargs):
            self.logger.info(f"{kwargs.get('name', serialized.get('name'))} starting")

    def on_chain_end(self, outputs, **kwargs):
        from apps.pipelines.models import PipelineRunStatus

        self.depth -= 1
        if self._should_log(kwargs):
            self.logger.info(f"{self._run_id_names[kwargs.get('run_id')]} finished: {outputs}")

        if self.depth == 0:
            self.pipeline_run.status = PipelineRunStatus.SUCCESS
            self.pipeline_run.save()  # We can only save in the original thread. Not totally clear why

    def on_chain_error(self, error, *args, **kwargs):
        from apps.pipelines.models import PipelineRunStatus

        self.pipeline_run.status = PipelineRunStatus.ERROR
        self.logger.error(error)


def get_logger(name, pipeline_run):
    log = logger.bind(name=name)
    log.level("DEBUG")
    log.remove()
    log.add(LogHandler(pipeline_run))
    return log


class LogHandler:
    def __init__(self, pipeline_run):
        self.pipeline_run = pipeline_run

    def __call__(self, message):
        from apps.pipelines.models import LogEntry

        record = message.record
        log_entry = LogEntry(
            **{
                "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f"),
                "level": record["level"].name,
                "message": record["message"],
            }
        )
        # Appending to a list is thread safe in python
        self.pipeline_run.log["entries"].append(log_entry.model_dump())

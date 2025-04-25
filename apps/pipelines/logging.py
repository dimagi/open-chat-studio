import uuid

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.types import Command
from loguru import logger


class LoggingCallbackHandler(BaseCallbackHandler):
    def __init__(self, verbose=False) -> None:
        self.verbose = verbose
        self._run_id_names = {}
        self._init_get_logger()

    def _init_get_logger(self):
        self.logger, self.log_entries = noop_logger()

    def _should_log(self, kwargs):
        """We only log steps that are defined nodes inside of the pipeline.

        The nodes themselves should handle logging further if more information is required.
        """

        if "langsmith:hidden" in kwargs.get("tags", []):
            return False

        if self.verbose:
            return True

        if kwargs.get("run_id") in self._run_id_names:
            return True

        if kwargs.get("name") in self.pipeline_run.pipeline.node_ids:
            self._run_id_names[kwargs.get("run_id")] = kwargs.get("name")
            return True

        return False

    def on_chain_start(self, serialized, inputs, *args, **kwargs):
        if self._should_log(kwargs):
            input = None
            if isinstance(inputs, str):
                input = inputs
            elif "messages" in inputs:
                input = inputs["messages"][-1]
            node_name = kwargs.get("name", serialized.get("name") if serialized else None)
            self.logger.info(
                f"{node_name} starting",
                input=input,
            )

    def on_chain_end(self, outputs, **kwargs):
        if self._should_log(kwargs):
            output = None
            if isinstance(outputs, str):
                output = outputs
            elif isinstance(outputs, Command):
                output = outputs.update
            elif "messages" in outputs:
                output = outputs["messages"][-1]
            self.logger.info(
                f"{self._run_id_names.get(kwargs.get('run_id'), '')} finished",
                output=output,
            )

    def on_chain_error(self, error, *args, **kwargs):
        self.logger.error(error)


class PipelineLoggingCallbackHandler(LoggingCallbackHandler):
    """Handles logging within the pipeline

    Since Langchain / Langgraph runs in separate threads, we append to a list
    in a thread-safe manner, then periodically write to the DB later.

    """

    def __init__(self, pipeline_run, verbose=False) -> None:
        self.pipeline_run = pipeline_run
        super().__init__(verbose)

    def _init_get_logger(self):
        self.logger = get_logger(uuid.uuid4().hex, self.pipeline_run)

    def on_chain_error(self, error, *args, **kwargs):
        from apps.pipelines.models import PipelineRunStatus

        self.pipeline_run.status = PipelineRunStatus.ERROR
        super().on_chain_error(error, *args, **kwargs)


def get_logger(name, pipeline_run) -> logger:
    log = logger.bind(name=name)
    log.level("DEBUG")
    log.remove()
    log.add(LogHandler(pipeline_run))
    return log


def noop_logger() -> tuple[logger, list]:
    log = logger.bind(name=uuid.uuid4().hex)
    log.level("DEBUG")
    log.remove()
    handler = MemLogHandler()
    log.add(handler)
    return log, handler.entries


class MemLogHandler:
    def __init__(self):
        self.entries = []

    def __call__(self, message):
        from apps.pipelines.models import LogEntry

        record = message.record

        output = record["extra"].get("output")
        input = record["extra"].get("input")
        log_entry = LogEntry(
            **{
                "time": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f"),
                "level": record["level"].name,
                "message": record["message"],
                "output": str(output) if output else None,
                "input": str(input) if input else None,
            }
        )
        self._save(log_entry)

    def _save(self, entry):
        self.entries.append(entry)


class LogHandler(MemLogHandler):
    def __init__(self, pipeline_run):
        super().__init__()
        self.pipeline_run = pipeline_run

    def _save(self, entry):
        # Appending to a list is thread safe in python
        self.pipeline_run.log["entries"].append(entry.model_dump())
        self.pipeline_run.save()

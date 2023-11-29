import dataclasses
from datetime import datetime
from typing import TextIO


@dataclasses.dataclass
class LogEntry:
    level: str
    message: str
    metadata: dict
    timestamp: datetime = dataclasses.field(init=False, default_factory=datetime.utcnow)

    def __str__(self, fmt: str = None):
        return self.format("[{level}] ({ts}): {message} {metadata}")

    def format(self, fmt):
        return fmt.format(ts=self.timestamp, level=self.level, message=self.message, metadata=self.metadata or "")


class Logger:
    def __init__(self, stream: TextIO = None):
        self.log_stack = [[]]
        self.stream = stream

    def log_entries(self):
        return list(self.log_stack[-1])

    def debug(self, message, metadata=None):
        self._log("debug", message, metadata)

    def info(self, message, metadata=None):
        self._log("info", message, metadata)

    def warn(self, message, metadata=None):
        self._log("warn", message, metadata)

    def error(self, message, metadata=None):
        self._log("error", message, metadata)

    def _log(self, level, message, metadata=None):
        entry = LogEntry(level, message, metadata)
        self.log_stack[-1].append(entry)
        if self.stream:
            print(str(entry), flush=True, file=self.stream)

    def __enter__(self):
        self.log_stack.append([])
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.log_stack.pop(-1)

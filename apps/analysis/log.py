import dataclasses
from datetime import datetime
from enum import IntEnum, auto
from typing import TextIO


class LogLevel(IntEnum):
    DEBUG = auto()
    INFO = auto()
    WARN = auto()
    ERROR = auto()


@dataclasses.dataclass
class LogEntry:
    level: LogLevel
    message: str
    metadata: dict
    timestamp: datetime = dataclasses.field(init=False, default_factory=datetime.utcnow)

    def __str__(self, fmt: str = None):
        return self.format("[{level}] ({ts}): {message} {metadata}")

    def format(self, fmt):
        return fmt.format(ts=self.timestamp, level=self.level.name, message=self.message, metadata=self.metadata or "")

    def to_json(self):
        return {
            "level": self.level.name,
            "message": self.message,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class Logger:
    def __init__(self, stream: TextIO = None):
        self.log_stack = [[]]
        self.stream = stream

    def log_entries(self):
        return list(self.log_stack[-1])

    def debug(self, message, metadata=None):
        self._log(LogLevel.DEBUG, message, metadata)

    def info(self, message, metadata=None):
        self._log(LogLevel.INFO, message, metadata)

    def warn(self, message, metadata=None):
        self._log(LogLevel.WARN, message, metadata)

    def error(self, message, metadata=None):
        self._log(LogLevel.ERROR, message, metadata)

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

    def to_json(self, level=LogLevel.INFO):
        return {"entries": [entry.to_json() for entry in self.log_entries() if entry.level >= level]}

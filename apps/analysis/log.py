import dataclasses
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from enum import IntEnum, auto


class LogLevel(IntEnum):
    DEBUG = auto()
    INFO = auto()
    WARN = auto()
    ERROR = auto()

    def is_debug(self):
        return self == LogLevel.DEBUG


@dataclasses.dataclass
class LogEntry:
    level: LogLevel
    message: str
    logger: str = ""
    timestamp: datetime = dataclasses.field(default_factory=datetime.utcnow)

    def __str__(self, fmt: str = None):
        return self.format("[{level}] [{logger}] ({ts}): {message}")

    def format(self, fmt):
        return fmt.format(ts=self.timestamp, level=self.level.name, message=self.message, logger=self.logger)

    def to_json(self):
        return {
            "level": self.level.name,
            "message": self.message,
            "logger": self.logger,
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
        }

    @classmethod
    def from_json(cls, data):
        return cls(
            level=LogLevel[data["level"]],
            message=data["message"],
            logger=data["logger"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )

    def is_debug(self):
        return self.level == LogLevel.DEBUG

    def is_info(self):
        return self.level == LogLevel.INFO

    def is_warn(self):
        return self.level == LogLevel.WARN

    def is_error(self):
        return self.level == LogLevel.ERROR


class LogStream(ABC):
    @abstractmethod
    def write(self, entry: LogEntry):
        raise NotImplementedError


class StdoutLogStream(LogStream):
    def write(self, entry: LogEntry):
        print(entry)


class Logger:
    def __init__(self, stream: LogStream = None):
        self.log_stack = [[]]
        self.name_stack = ["root"]
        self.stream = stream

    def log_entries(self):
        return list(self.log_stack[-1])

    def debug(self, message):
        self._log(LogLevel.DEBUG, message)

    def info(self, message):
        self._log(LogLevel.INFO, message)

    def warn(self, message):
        self._log(LogLevel.WARN, message)

    def error(self, message):
        self._log(LogLevel.ERROR, message)

    def _log(self, level, message):
        entry = LogEntry(level, message, logger=self.name_stack[-1])
        self.log_stack[-1].append(entry)
        if self.stream:
            self.stream.write(entry)

    @contextmanager
    def __call__(self, name: str):
        self.log_stack.append([])
        self.name_stack.append(name)
        try:
            yield self
        finally:
            self.name_stack.pop(-1)
            self.log_stack.pop(-1)

    def to_json(self, level=LogLevel.INFO):
        return {"entries": [entry.to_json() for entry in self.log_entries() if entry.level >= level]}

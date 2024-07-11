from typing import Any

from .models import Usage, UsageType


class UsageRecorder:
    def __init__(self, team, source: Any):
        self.team = team
        self.usage = []
        self.source = source
        self.stack = []

    def __enter__(self):
        self.stack.append(1)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.usage:
            Usage.objects.bulk_create(self.usage)
            self.usage = []
        self.stack.pop()

    def _ensure_open(self):
        if not self.stack:
            raise RuntimeError("UsageRecorder must be used as a context manager")

    def record_token_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        if input_tokens:
            self._record_token_usage(input_tokens, UsageType.INPUT_TOKENS)
        if output_tokens:
            self._record_token_usage(output_tokens, UsageType.OUTPUT_TOKENS)

    def _record_token_usage(self, tokens: int, usage_type: UsageType):
        self.usage.append(Usage(team=self.team, content_object=self.source, type=usage_type, value=tokens))

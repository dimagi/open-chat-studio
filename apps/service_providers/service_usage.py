from contextlib import contextmanager

from apps.accounting.usage import UsageRecorder
from apps.teams.models import BaseTeamModel


class UsageMixin:
    usage_recorder: UsageRecorder

    @contextmanager
    def record_usage(self, source_object: BaseTeamModel, metadata: dict = None):
        with self.usage_recorder.for_source(source_object, metadata=metadata):
            yield self.usage_recorder

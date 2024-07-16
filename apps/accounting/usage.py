import dataclasses
import logging
import os
import threading
from collections import Counter
from contextlib import contextmanager
from typing import Any

from apps.teams.models import BaseTeamModel

from .models import Usage, UsageType

log = logging.getLogger("audit")


class UsageOutOfScopeError(Exception):
    pass


@dataclasses.dataclass
class UsageRecord:
    """A record of usage for a specific service object.
    This is intentionally separate from the Django model to make testing
    easier and faster.
    """

    team_id: int
    source_object: BaseTeamModel
    service_object: BaseTeamModel
    type: UsageType
    value: int
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def get_model(self):
        return Usage(
            team_id=self.team_id,
            source_object=self.source_object,
            service_object=self.service_object,
            type=self.type,
            value=self.value,
            metadata=self.metadata,
        )


@dataclasses.dataclass
class UsageScope:
    service_object: BaseTeamModel
    source_object: BaseTeamModel
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        if self.service_object.team_id != self.source_object.team_id:
            raise ValueError("Source object must belong to the same team as the service object")

    @property
    def team_id(self) -> int:
        return self.service_object.team_id

    def get_usage(self, usage_type: UsageType, value: int, metadata: dict = None):
        return UsageRecord(
            team_id=self.team_id,
            source_object=self.source_object,
            service_object=self.service_object,
            type=usage_type,
            value=value,
            metadata=self.metadata | (metadata or {}),
        )


class BaseUsageRecorder:
    def __init__(self):
        self._lock = threading.Lock()
        self.usage = []
        self.scope: list[UsageScope] = []
        self.totals = Counter()

    @contextmanager
    def for_source(self, source_object: BaseTeamModel, metadata: dict = None):
        with self._lock:
            self.scope.append(self._get_scope(source_object, metadata))
        try:
            yield
        finally:
            self.maybe_commit()

    def _get_scope(self, source_object: BaseTeamModel, metadata: dict = None):
        raise NotImplementedError()

    @contextmanager
    def update_metadata(self, metadata: dict):
        """Context manager to temporarily update the metadata for the current scope."""
        with self._lock:
            current_scope = self.get_current_scope()
            self.scope.append(
                self._get_scope(current_scope.source_object, metadata=current_scope.metadata | (metadata or {}))
            )

        try:
            yield
        finally:
            with self._lock:
                self.scope.pop()

    def maybe_commit(self):
        with self._lock:
            self.scope.pop()
            if not self.scope:
                self.commit_and_clear()

    def commit_and_clear(self):
        batch = self.get_batch()
        if batch:
            Usage.objects.bulk_create([usage.get_model() for usage in batch])
            for usage in batch:
                self.totals[usage.type] += usage.value
        self.usage = []

    def get_batch(self):
        usages = {}
        for usage in self.usage:
            key = (usage.type, frozenset(usage.metadata.items()))
            if key not in usages:
                usages[key] = []
            usages[key].append(usage)

        return [_merge_usages(usage) for usage in usages.values()]

    def get_current_scope(self):
        if not self.scope:
            if os.getenv("UNIT_TESTING", False):
                raise UsageOutOfScopeError("UsageRecorder must be used as a context manager")
            else:
                log.exception("Missing scope for usage recording. User `service.usage_scope` context manager.")
        return self.scope[-1]

    def record_usage(self, usage_type: UsageType, value, metadata: dict = None):
        current_scope = self.get_current_scope()
        self.usage.append(current_scope.get_usage(usage_type, value, metadata))


class UsageRecorder(BaseUsageRecorder):
    def __init__(self, service_object: BaseTeamModel):
        super().__init__()
        self.service_object = service_object

    def _get_scope(self, source_object: BaseTeamModel, metadata: dict = None):
        return UsageScope(service_object=self.service_object, source_object=source_object, metadata=metadata or {})


def _merge_usages(usages: list[UsageRecord]):
    return UsageRecord(
        team_id=usages[0].team_id,
        source_object=usages[0].source_object,
        service_object=usages[0].service_object,
        type=usages[0].type,
        value=sum([u.value for u in usages]),
        metadata=usages[0].metadata,
    )

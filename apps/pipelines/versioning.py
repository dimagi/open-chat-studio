"""Registry of node params that reference database records.

Each spec declares how a node param holding a model ID is handled when the node is
versioned (publish), when a node version is archived, and how it is resolved for
version-detail display. Adding a new param that references a versioned model only
requires a new spec here; the code in ``apps.pipelines.models.Node`` is driven
entirely by this registry.

Custom actions are not included: they are linked through ``CustomActionOperation``
records rather than an ID param, and are handled by ``CustomActionOperationMixin``.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

versioning_logger = logging.getLogger("ocs.versioning")


class ParamVersioning(StrEnum):
    """How the referenced record is treated when the node is versioned."""

    NEW_VERSION = "new_version"
    """Always create a new version of the referenced record and point the param at it."""

    REUSE_UNCHANGED = "reuse_unchanged"
    """Create a new version only if the record changed since its latest version,
    otherwise point the param at the existing latest version."""

    LIVE_REFERENCE = "live_reference"
    """The record is a live shared resource; the param keeps the working ID verbatim."""


class ParamArchiving(StrEnum):
    """How the referenced record is treated when the node version is archived."""

    ARCHIVE = "archive"
    """Archive the referenced record."""

    ARCHIVE_VERSIONS_ONLY = "archive_versions_only"
    """Archive the referenced record only if it is a version, never a live working record."""

    KEEP = "keep"
    """Leave the referenced record untouched."""


@dataclass(frozen=True)
class VersionedParamSpec:
    param_name: str
    model_label: str  # "app_label.ModelName"; resolved lazily to avoid circular imports
    display_name: str
    versioning: ParamVersioning
    archiving: ParamArchiving
    many: bool = False  # param holds a list of IDs

    def __post_init__(self):
        if self.many and self.versioning != ParamVersioning.LIVE_REFERENCE:
            raise ValueError("Versioning of multi-ID params is not supported")

    @property
    def model_cls(self):
        return apps.get_model(self.model_label)

    def version_referenced_record(self, params: dict) -> None:
        """Version the referenced record according to the spec's versioning strategy,
        updating the param in ``params`` to point at the version."""
        instance_id = params.get(self.param_name)
        if not instance_id:
            return

        match self.versioning:
            case ParamVersioning.LIVE_REFERENCE:
                return
            case ParamVersioning.NEW_VERSION:
                instance = self.model_cls.objects.get(id=instance_id)
                if not instance.is_a_version:
                    params[self.param_name] = str(instance.create_new_version().id)
            case ParamVersioning.REUSE_UNCHANGED:
                instance = self.model_cls.objects.filter(id=instance_id).first()
                if instance:
                    if not instance.has_versions or instance.compare_with_latest():
                        params[self.param_name] = str(instance.create_new_version().id)
                    else:
                        params[self.param_name] = str(instance.latest_version.id)

    def revert_referenced_record(self, params: dict) -> None:
        """Inverse of ``version_referenced_record``: rewrite the param from a versioned
        record id back to the id of its working version.

        Used by revert, which reconstructs the working pipeline from a version's data.
        ``LIVE_REFERENCE`` params keep their id verbatim (publish never rewrote them)."""
        if self.versioning == ParamVersioning.LIVE_REFERENCE:
            return

        instance_id = params.get(self.param_name)
        if not instance_id:
            return

        instance = self.model_cls.objects.filter(id=instance_id).first()
        if instance:
            params[self.param_name] = str(instance.get_working_version_id())

    def archive_referenced_record(self, params: dict) -> None:
        """Archive the record(s) referenced by the param according to the spec's
        archiving strategy."""
        if self.archiving == ParamArchiving.KEEP:
            return

        value = params.get(self.param_name)
        instance_ids = (value if self.many else [value]) if value else []
        for instance_id in instance_ids:
            try:
                instance = self.model_cls.objects.get(id=instance_id)
            except ObjectDoesNotExist:
                versioning_logger.exception(
                    f"Failed to archive {self.param_name} with id {instance_id}, since it could not be found"
                )
                continue
            if self.archiving == ParamArchiving.ARCHIVE_VERSIONS_ONLY and not instance.is_a_version:
                continue
            instance.archive()

    def resolve_for_display(self, value):
        """Resolve the raw param value to model instance(s) for version-detail display."""
        if not value:
            return value
        if self.many:
            return list(self.model_cls.objects.filter(id__in=value))
        return self.model_cls.objects.filter(id=value).first()


_NODE_PARAM_SPECS: dict[str, tuple[VersionedParamSpec, ...]] = {
    "AssistantNode": (
        VersionedParamSpec(
            param_name="assistant_id",
            model_label="assistants.OpenAiAssistant",
            display_name="assistant",
            versioning=ParamVersioning.NEW_VERSION,
            archiving=ParamArchiving.ARCHIVE,
        ),
    ),
    "LLMResponseWithPrompt": (
        VersionedParamSpec(
            param_name="source_material_id",
            model_label="experiments.SourceMaterial",
            display_name="source_material",
            versioning=ParamVersioning.REUSE_UNCHANGED,
            # Archiving source material versions when the node is archived is still a TODO
            archiving=ParamArchiving.KEEP,
        ),
        # ADR-0031: collections (media + index) are live shared resources. Only frozen
        # per-bot versions (legacy data) are ever archived; never the live working collection.
        VersionedParamSpec(
            param_name="collection_id",
            model_label="documents.Collection",
            display_name="media",
            versioning=ParamVersioning.LIVE_REFERENCE,
            archiving=ParamArchiving.ARCHIVE_VERSIONS_ONLY,
        ),
        VersionedParamSpec(
            param_name="collection_index_ids",
            model_label="documents.Collection",
            display_name="Collection Indexes",
            versioning=ParamVersioning.LIVE_REFERENCE,
            archiving=ParamArchiving.ARCHIVE_VERSIONS_ONLY,
            many=True,
        ),
    ),
}


def get_versioned_param_specs(node_type: str) -> tuple[VersionedParamSpec, ...]:
    return _NODE_PARAM_SPECS.get(node_type, ())

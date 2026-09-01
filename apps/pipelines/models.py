import contextlib
import copy
import logging
from collections import defaultdict
from collections.abc import Iterator
from functools import cached_property
from uuid import uuid4

import pydantic
from django.db import DatabaseError, models, transaction
from django.urls import reverse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from apps.chat.models import ChatMessageType
from apps.custom_actions.form_utils import set_custom_actions
from apps.custom_actions.mixins import CustomActionOperationMixin
from apps.experiments.models import ExperimentSession, VersionFieldDisplayFormatters
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.pipelines.exceptions import (
    ErrorReport,
    MissingNodeDataError,
    PipelineBuildError,
    PipelineNodeBuildError,
    error_report,
    has_errors,
)
from apps.pipelines.flow import (
    Flow,
    FlowNode,
    FlowNodeData,
    FlowWithoutNodes,
    node_position_fields,
    react_flow_node_type,
)
from apps.pipelines.helper import create_pipeline_with_nodes, duplicate_pipeline_with_new_ids
from apps.pipelines.versioning import get_versioned_param_specs
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.fields import SanitizedJSONField, as_int
from apps.utils.llm_messages import ensure_non_empty_text
from apps.utils.models import BaseModel

logger = logging.getLogger("ocs.pipelines")


class PipelineManager(VersionsObjectManagerMixin, models.Manager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                is_version=models.Case(
                    models.When(working_version_id__isnull=False, then=True),
                    models.When(working_version_id__isnull=True, then=False),
                    output_field=models.BooleanField(),
                )
            )
        )


class NodeObjectManager(VersionsObjectManagerMixin, models.Manager):
    def llm_response_with_prompt_nodes(self):
        from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415 - circular: nodes.nodes→models

        return self.get_queryset().filter(type=LLMResponseWithPrompt.__name__)

    def assistant_nodes(self):
        from apps.pipelines.nodes.nodes import AssistantNode  # noqa: PLC0415 - circular: nodes.nodes→models

        return self.get_queryset().filter(type=AssistantNode.__name__)


#: What a caller has to prefetch before reading a pipeline's graph. ``Node.resource_params`` reads
#: these related rows per node, so ``flow_data`` is an N+1 without them.
NODE_RESOURCE_PREFETCHES = ("node_set__collection_indexes", "node_set__custom_action_operations")


class Pipeline(BaseTeamModel, VersionsMixin):
    name = models.CharField(max_length=128)
    data = SanitizedJSONField()
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    edit_revision = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)

    objects = PipelineManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.working_version is None:
            return self.name
        return f"{self.name} ({self.version_display})"

    @property
    def version_display(self) -> str:
        if self.is_working_version:
            return ""
        return f"v{self.version_number}"

    @classmethod
    def create_default_pipeline_with_name(cls, team, name, llm_provider_id=None, llm_provider_model=None):
        return cls.create_default(team, name, llm_provider_id, llm_provider_model)

    @classmethod
    def create_default(cls, team, name=None, llm_provider_id=None, llm_provider_model=None):
        default_name = "New Pipeline" if name is None else name
        existing_pipeline_count = cls.objects.filter(team=team, name__startswith=default_name).count()

        node = None
        if llm_provider_id and llm_provider_model:
            llm_id = f"LLMResponseWithPrompt-{uuid4().hex[:5]}"
            node = FlowNode(
                id=llm_id,
                type="pipelineNode",
                position={"x": 300, "y": 0},
                data=FlowNodeData(
                    id=llm_id,
                    type="LLMResponseWithPrompt",
                    label="LLM",
                    params={
                        "name": llm_id,
                        "llm_provider_id": llm_provider_id,
                        "llm_provider_model_id": llm_provider_model.id,
                        "llm_temperature": 0.7,
                        "history_type": "global",
                        "history_name": None,
                        "history_mode": "summarize",
                        "user_max_token_limit": llm_provider_model.max_token_limit,
                        "max_history_length": 10,
                        "source_material_id": None,
                        "prompt": "You are a helpful assistant. Answer the user's query as best you can.",
                        "tools": None,
                        "custom_actions": None,
                        "keywords": [""],
                    },
                ),
            )

        final_name = default_name if name else f"New Pipeline {existing_pipeline_count + 1}"
        return create_pipeline_with_nodes(team=team, name=final_name, middle_node=node)

    def get_absolute_url(self):
        return reverse("pipelines:edit", args=[get_slug_for_team(self.team_id), self.id])

    @transaction.atomic()
    def update_nodes_from_data(self, node_data: dict[str, FlowNode | None]) -> None:
        """Reconcile this pipeline's ``Node`` rows against ``node_data``.

        ``node_data`` is the complete graph membership (``self.data`` no longer lists nodes —
        ADR-0049): rows whose flow_id is absent are deleted, or archived when they have
        versions. A ``FlowNode`` carrying content creates or updates its row, position columns
        included (they are the authoritative layout source for reads). A membership-only entry
        (``None``, or a ``FlowNode`` with no ``data``) leaves an existing row untouched and is
        an error when no row exists.

        Because an incomplete mapping means "delete the rest", the mapping is validated
        before anything is removed, and the whole reconcile runs in one transaction: a
        caller that passes a bad mapping gets an exception, not a pipeline with no nodes.
        """
        current_ids = set(self.node_ids)
        membership_only = {flow_id for flow_id, node in node_data.items() if node is None or node.data is None}
        missing = membership_only - current_ids
        if missing:
            raise MissingNodeDataError(missing)

        to_remove = current_ids - set(node_data)
        pipeline_nodes = Node.objects.annotate(versions_count=models.Count("versions")).filter(
            pipeline=self, flow_id__in=to_remove
        )
        nodes_to_archive = pipeline_nodes.filter(versions_count__gt=0)
        pipeline_nodes.filter(versions_count=0).delete()

        for node in nodes_to_archive:
            # Preserve the node if it has versions, otherwise we tamper with previous versions
            node.archive()

        for flow_id, node in node_data.items():
            if flow_id in membership_only:
                continue
            if node.id != flow_id:
                # The key is the flow_id the content is written under, so a mismatch would
                # silently store this node's content on a different row.
                raise ValueError(f"node_data key {flow_id!r} does not match node id {node.id!r}")
            content = node.data
            created_node, _ = Node.objects.update_or_create(
                pipeline=self,
                flow_id=flow_id,
                defaults={
                    "type": content.type,
                    "params": content.params,
                    "label": content.label,
                    **node_position_fields(node.position),
                },
            )
            created_node.update_from_params()

    def clear_node_caches(self) -> None:
        """Re-read the ``Node`` rows and drop the ``flow_data`` built from the stale ones.

        ``update_nodes_from_data`` writes rows straight to the database, behind the back of a
        prefetched ``node_set`` and of ``flow_data``'s cache. A caller that reads either after
        reconciling must call this first or it sees the pre-reconcile graph.

        The rows are re-prefetched with their resource relations rather than just dropped, since
        every caller here reads ``flow_data`` next and that reads them per node.
        """
        # No fields: clears the whole prefetch cache, node_set included.
        self.refresh_from_db()
        with contextlib.suppress(AttributeError):  # nothing cached if flow_data was never read
            del self.flow_data
        models.prefetch_related_objects([self], *NODE_RESOURCE_PREFETCHES)

    def validate(self, full=True) -> ErrorReport:
        """Every problem with this pipeline. All three buckets empty means it is valid.

        Callers should test the result with :func:`~apps.pipelines.exceptions.has_errors` rather than
        for truthiness — the report is always fully populated, so an error-free one is still a dict
        with three empty values.
        """
        from apps.pipelines.graph import PipelineGraph  # noqa: PLC0415 - circular: graph.py imports models

        errors = defaultdict(dict)
        nodes = self.node_set.all()
        for node in nodes:
            if node_errors := self._node_validation_errors(node):
                errors[node.flow_id].update(node_errors)

        name_to_flow_id = defaultdict(list)
        for node in nodes:
            name_to_flow_id[node.params.get("name")].append(node.flow_id)

        for _name, flow_ids in name_to_flow_id.items():
            if len(flow_ids) > 1:
                for flow_id in flow_ids:
                    errors[flow_id].update({"name": "All node names must be unique"})

        if not full:
            return error_report(errors, [])

        graph = PipelineGraph.build_from_pipeline(self)
        report = error_report(errors, graph.build_errors)

        # The remaining checks — building each node instance, then compiling — genuinely require
        # everything above to pass, so they stay staged behind it. Skipping them when the report is
        # already non-empty also means an invalid pipeline no longer pays for a doomed build.
        if not has_errors(report):
            try:
                graph.build_runnable()
            except PipelineBuildError as e:
                report = error_report(errors, [e])
            except PipelineNodeBuildError:
                # Not a PipelineBuildError subclass, and carries no node id of its own. Every node
                # validated cleanly above or this branch would not have run, so what lands here is a
                # build-stage failure the node checks could not name — and its message may be a raw
                # pydantic dump wrapped at the build site, naming the classes behind the node.
                #
                # Logged rather than reported, for the same reason as the catch-all in
                # ``_node_validation_errors``: the report is served over the API.
                logger.exception("Pipeline %s could not be built", self.id)
                report["pipeline"].append("This pipeline could not be built. Check the values of its nodes' params.")

        return report

    @staticmethod
    def _node_validation_errors(node) -> dict:
        """Field -> message errors for one node's params; non-field failures land under "root"."""
        from apps.pipelines.nodes.base import resolve_node_class  # noqa: PLC0415 - heavy: nodes→langgraph

        node_class = resolve_node_class(node.type)
        if node_class is None:
            # A type naming no node class — removed since, or never one — must be reported, not crash
            # validation.
            return {"root": f"Unknown node type: {node.type}"}
        try:
            node_class.model_validate({**node.params, "node_id": node.flow_id, "django_node": node})
        except pydantic.ValidationError as e:
            # A model-level error carries no ``loc`` and names its field in ``ctx`` instead (see the
            # PydanticCustomError raises under apps/pipelines/nodes). A validator raising a plain
            # ValueError has neither, so that error lands on the node as a whole.
            return {
                (error["loc"][0] if error["loc"] else error.get("ctx", {}).get("field", "root")): error["msg"]
                for error in e.errors()
            }
        except PipelineNodeBuildError as e:
            # Raised from inside a validator for a broken resource reference (e.g. a deleted
            # provider model); pydantic doesn't wrap it, so fold it into the report here.
            return {"root": str(e)}
        except DatabaseError:
            # Never swallowed: inside an atomic block a caught database error leaves the transaction
            # aborted, so reporting it as a node error would raise again on the next query.
            raise
        except Exception:  # noqa: BLE001 - a broken node must not take the whole read down
            # Anything a validator raises before pydantic can wrap it — a wrong-typed param reaching
            # a `mode="before"` validator, say. This runs on every read of a pipeline, so an
            # unparseable node has to be reportable rather than 500 /inspect/ for good.
            #
            # Logged rather than reported: this branch catches anything at all, so the message is as
            # likely to expose how the server is put together as to say something useful, and the
            # report is served over the API.
            logger.exception("Node %s of pipeline %s could not be validated", node.flow_id, node.pipeline_id)
            return {"root": "This node could not be read. Check the values of its params."}
        return {}

    @property
    def data_without_positions(self):
        """The full flow (node content reconstructed from the Node rows) minus layout positions."""
        if not self.data:
            return self.data
        return {
            **{key: value for key, value in self.data.items() if key != "nodes"},
            "nodes": [{k: v for k, v in node.items() if k != "position"} for node in self.flow_data["nodes"]],
        }

    @cached_property
    def flow_data(self) -> dict:
        """The full react-flow graph, rebuilt from the ``Node`` rows.

        ``self.data`` supplies only the edges; each node's content, layout position and
        react-flow type come from its ``Node`` row (ADR-0049).
        """
        # ``edges`` is required, so stand in for data that is empty or missing entirely; the
        # rows still describe a graph. Same trigger as ``data_without_positions``' guard but a
        # different fallback: that one returns the empty data as-is rather than rebuilding.
        flow = Flow(**(self.data or {"edges": []}))
        # Each node reads related resource rows (Node.resource_params), so callers fetch the
        # pipeline with ``NODE_RESOURCE_PREFETCHES`` prefetched or this is an N+1.
        flow.nodes = [node.to_flow_node() for node in self.node_set.all()]
        return flow.model_dump()

    @property
    def node_ids(self):
        return self.node_set.order_by("created_at").values_list("flow_id", flat=True).all()

    @transaction.atomic()
    def create_new_version(self, is_copy: bool = False):  # ty: ignore[invalid-method-override]
        version_number = 1 if is_copy else self.version_number
        if not is_copy:
            self.version_number = self.version_number + 1
            self.save(update_fields=["version_number"])
        pipeline_version = super().create_new_version(save=False, is_copy=is_copy)
        pipeline_version.version_number = version_number
        id_mapping = {}
        if is_copy:
            node_types = dict(self.node_set.values_list("flow_id", "type"))
            data, id_mapping = duplicate_pipeline_with_new_ids(self.data, node_types)
            pipeline_version.data = data
        pipeline_version.save()
        for node in self.node_set.all():
            node.create_new_version(
                is_copy=is_copy, new_flow_id=id_mapping.get(node.flow_id), pipeline=pipeline_version
            )

        return pipeline_version

    @transaction.atomic()
    def revert_to_version(self, version: "Pipeline") -> None:
        """Reset this working pipeline to the state of ``version``.

        Takes the version's node rows one at a time and remaps params that reference
        versioned records back to their working id — the inverse of the rewriting done
        during publish, see ``apps.pipelines.versioning`` — then persists the version's
        edges and rebuilds every working node from that content via
        ``update_nodes_from_data``. The versioned record for each param is read from the
        version node's resource FK column.

        The version's stored ``data`` supplies the edges only.
        """
        node_data: dict[str, FlowNode | None] = {}
        # to_flow_node reads each node's resource relations — prefetch rather than query per row.
        for version_node in version.node_set.prefetch_related("collection_indexes", "custom_action_operations"):
            flow_node = version_node.to_flow_node()
            for spec in get_versioned_param_specs(version_node.type):
                spec.revert_referenced_record(version_node, flow_node.data.params)
            node_data[version_node.flow_id] = flow_node

        self.data = FlowWithoutNodes(**(version.data or {"edges": []})).model_dump()
        self.edit_revision += 1
        self.save(update_fields=["data", "edit_revision"])
        self.update_nodes_from_data(node_data)
        # The rows were written straight to the DB, so hand back an instance that reads as the
        # reverted pipeline rather than the one it replaced. Same reason the save paths do it.
        self.clear_node_caches()

    @transaction.atomic()
    def archive(self) -> bool:
        """
        Archive this record only when it is not still being referenced by other records. If this record is the working
        version, all of its versions will be archived as well. The same goes for its nodes.
        """
        if self.get_related_experiments_queryset().exists():
            return False

        if len(self.get_static_trigger_experiment_ids()) > 0:
            return False

        super().archive()
        for node in self.node_set.all():
            node.archive()

        if self.is_working_version:
            for version in self.versions.filter(is_archived=False):
                version.archive()

        return True

    @transaction.atomic()
    def unarchive(self):
        """Reverse of archive(): this pipeline and its archived nodes.

        For version pipelines only — those are archived as a unit with their experiment version
        (see ``Experiment.archive``), so every archived node belongs to that unit. The working
        pipeline is never archived that way: its archived nodes are the ones the user deleted from
        the canvas (``update_nodes_from_data`` keeps a versioned node as an archived row), and its
        archived versions belong to experiment versions archived in their own right. Neither may
        be restored wholesale.
        """
        super().unarchive()
        for node in self.node_set.get_all().filter(is_archived=True):
            node.unarchive()

    def get_node_param_values(self, node_cls, param_name: str) -> list:
        return list(self.node_set.filter(type=node_cls.__name__).values_list(f"params__{param_name}", flat=True))

    def get_related_experiments_queryset(self) -> models.QuerySet:
        return self.experiment_set.filter(is_archived=False)

    def get_static_trigger_experiment_ids(self) -> models.QuerySet:
        from apps.events.models import (  # noqa: PLC0415 - circular: events.models→pipelines.models
            EventAction,
            EventActionType,
        )

        return (
            EventAction.objects.filter(
                action_type=EventActionType.PIPELINE_START,
                params__pipeline_id=self.id,
                static_trigger__is_archived=False,
            )
            .annotate(trigger_experiment_id=models.F("static_trigger__experiment"))
            .values("trigger_experiment_id")
        )

    def _get_version_details(self) -> VersionDetails:
        reserved_types = ["StartNode", "EndNode"]

        def node_name(node):
            name = node.params.get("name")
            if name == node.flow_id:
                return node.type
            return name

        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="name", raw_value=self.name),
                VersionField(
                    name="nodes",
                    queryset=self.node_set.exclude(type__in=reserved_types),
                    to_display=node_name,
                ),
            ],
        )


class Node(BaseModel, VersionsMixin, CustomActionOperationMixin):
    flow_id = models.CharField(max_length=128, db_index=True)  # The ID assigned by react-flow
    type = models.CharField(max_length=128)  # The node type, should be one from nodes/nodes.py
    label = models.CharField(max_length=128, blank=True, default="")  # The human readable label
    params = SanitizedJSONField(default=dict)  # Parameters for the specific node type
    # Layout position on the editor canvas (ADR-0049) — the authoritative source for reads.
    # Null until the row is saved, or until migration 0030 backfills it from the old blob.
    position_x = models.FloatField(null=True, blank=True)
    position_y = models.FloatField(null=True, blank=True)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE)
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
    )
    llm_provider_model = models.ForeignKey(
        "service_providers.LlmProviderModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
    )
    source_material = models.ForeignKey(
        "experiments.SourceMaterial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
    )
    collection = models.ForeignKey(
        "documents.Collection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="media_nodes",
    )
    collection_indexes = models.ManyToManyField(
        "documents.Collection",
        blank=True,
        related_name="index_nodes",
    )
    assistant = models.ForeignKey(
        "assistants.OpenAiAssistant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
    )
    synthetic_voice = models.ForeignKey(
        "experiments.SyntheticVoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nodes",
    )
    objects = NodeObjectManager()

    def __str__(self):
        return self.flow_id

    @property
    def name(self):
        return self.params.get("name", None)

    @property
    def position(self) -> dict | None:
        """The react-flow position, or None when the row has not been backfilled yet."""
        if self.position_x is None or self.position_y is None:
            return None
        return {"x": self.position_x, "y": self.position_y}

    def to_flow_node(self) -> FlowNode:
        """This row as a react-flow node, content included.

        ``params`` is deep-copied so a caller that rewrites the returned node's params
        (``Pipeline.revert_to_version``) cannot reach back into this row's stored dict, and the
        resource ids it duplicates are re-read from the FK columns (see ``resource_params``).
        """
        params = copy.deepcopy(self.params or {})
        params.update(self.resource_params())
        return FlowNode(
            id=self.flow_id,
            position=self.position or {"x": 0, "y": 0},
            type=react_flow_node_type(self.type),
            data=FlowNodeData(
                id=self.flow_id,
                type=self.type,
                params=params,
                label=self.label,
            ),
        )

    def has_parameter(self, param_name: str) -> bool:
        """True if this node's type declares ``param_name`` as a param. Unknown types have none."""
        from apps.pipelines.nodes.base import resolve_node_class  # noqa: PLC0415 - heavy: nodes→langgraph

        node_class = resolve_node_class(self.type)
        return node_class is not None and param_name in node_class.model_fields

    def create_new_version(self, is_copy=False, new_flow_id=None, pipeline=None):  # ty: ignore[invalid-method-override]
        """
        Create a new version of the node. Params that reference versioned records (see
        `apps.pipelines.versioning`) are versioned along with the node and updated to point at the new version.

        Args:
            pipeline: If provided, the new version will be assigned to this pipeline before saving,
                avoiding a transient state where the node temporarily belongs to the original pipeline.
        """
        new_version = super().create_new_version(save=False, is_copy=is_copy)
        if is_copy and new_flow_id:
            old_flow_id = new_version.flow_id
            new_version.flow_id = new_flow_id
            if new_version.type not in ("StartNode", "EndNode") and new_version.params.get("name") == old_flow_id:
                new_version.params["name"] = new_flow_id

        if not is_copy:
            for spec in get_versioned_param_specs(self.type):
                spec.version_referenced_record(new_version.params)

        if pipeline is not None:
            new_version.pipeline = pipeline
        new_version.save()
        if self.params.get("custom_actions"):
            self._copy_custom_action_operations_to_new_version(new_node=new_version, is_copy=is_copy)
        new_version._sync_resource_fk_fields()

        return new_version

    def set_params(self, params):
        """Assign params, persist them, and re-derive the resource FK mirror.

        Prefer this over assigning ``self.params`` and calling ``save()`` directly: it keeps
        the FK columns (a derived mirror of the ids in params) from drifting away from params.
        See ``_sync_resource_fk_fields``.
        """
        self.params = params
        # Persist only params (not a full save of a possibly-stale instance) so concurrent
        # writes to unrelated columns aren't clobbered. _sync_resource_fk_fields handles the
        # derived FK columns.
        self.save(update_fields=["params"])
        self._sync_resource_fk_fields()

    def update_from_params(self):
        """Callback to do DB related updates pertaining to the node params"""
        from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415 - circular: nodes.nodes→models

        self._sync_resource_fk_fields()

        if self.type == LLMResponseWithPrompt.__name__:
            custom_action_infos = []
            for custom_action_operation in self.params.get("custom_actions") or []:
                custom_action_id, operation_id = custom_action_operation.split(":")
                custom_action_infos.append({"custom_action_id": custom_action_id, "operation_id": operation_id})

            set_custom_actions(self, custom_action_infos)

    @classmethod
    def resource_fk_fields(cls):
        return [
            field.name
            for field in cls._meta.get_fields()
            if isinstance(field, models.ForeignKey) and field.remote_field.on_delete is models.SET_NULL
        ]

    @classmethod
    def resource_param_names(cls) -> frozenset[str]:
        """The param names ``resource_params`` produces, whatever the node's type.

        ``to_flow_node`` merges all of these into every node's params, its type declaring them or
        not, so a caller writing params back has to tell the mirrored ids from the declared ones.
        """
        return frozenset(
            {f"{field_name}_id" for field_name in cls.resource_fk_fields()} | {"collection_index_ids", "custom_actions"}
        )

    def resource_params(self) -> dict:
        """The resources this node references, read off its FK columns, its M2M and its related rows.

        Params carries copies of these ids, but the rows are the constraint-backed mirror of them
        (see ``_sync_resource_fk_fields`` and ``update_from_params``): deleting a resource nulls the
        column or cascades the row away while the stale id lingers in params. ``to_flow_node`` serves
        what this returns, so a dangling reference reads as unset rather than as an id that no longer
        resolves.
        """
        resource_params = {
            f"{field_name}_id": getattr(self, f"{field_name}_id") for field_name in self.resource_fk_fields()
        }
        # sorted because the related rows have no ordering of their own and the read must be stable.
        resource_params["collection_index_ids"] = sorted(index.id for index in self.collection_indexes.all())
        # ``CustomActionOperation`` rows are the mirror for ``custom_actions``: ``update_from_params``
        # writes them, and deleting the action cascades them away. Read here in the param's own
        # ``"<action id>:<operation id>"`` spelling.
        resource_params["custom_actions"] = sorted(
            operation.get_model_id(with_holder=False) for operation in self.custom_action_operations.all()
        )
        return resource_params

    def _sync_resource_fk_fields(self):
        """Populate FK/M2M fields from the params JSON.

        The FK columns are a derived mirror of the IDs in params (non-int/boolean values
        map to null). A dangling scalar id is coerced to null rather than written straight
        through: not every SET_NULL resource is protected by a delete guard (an LlmProvider,
        for one, can be deleted while a node references it — SET_NULL nulls the FK column but
        the stale id lingers in params), so re-deriving it verbatim would resurrect the
        dangling reference and trip the deferred DB FK constraint at commit. Existence is
        checked against ``_base_manager`` so a still-valid reference to a soft-deleted
        (archived) resource isn't mistaken for dangling. This mirrors the collection_indexes
        M2M below and the ``backfill_node_fks`` command. Versions may point at a since-deleted
        resource, but they're never re-synced. Only saves when a scalar FK changed.
        """
        from apps.documents.models import Collection  # noqa: PLC0415 - avoid circular import

        params = self.params or {}
        update_fields = []
        for field_name in self.resource_fk_fields():
            value = as_int(params.get(f"{field_name}_id"))
            if value is not None:
                related_model = self._meta.get_field(field_name).related_model
                if not related_model._base_manager.filter(pk=value).exists():
                    value = None
            if getattr(self, f"{field_name}_id") != value:
                setattr(self, f"{field_name}_id", value)
                update_fields.append(f"{field_name}_id")
        if update_fields:
            self.save(update_fields=update_fields)

        raw_index_ids = params.get("collection_index_ids") or []
        if not isinstance(raw_index_ids, list | tuple | set):
            raw_index_ids = [raw_index_ids]
        # Coerce through as_int (like the scalar FKs) so malformed JSON values are dropped rather
        # than blowing up the id__in query.
        index_ids = [parsed for parsed in map(as_int, raw_index_ids) if parsed is not None]
        self.collection_indexes.set(Collection.objects.filter(id__in=index_ids))

    def archive(self):
        """
        Archiving a node will also archive the assistant if it is an assistant node. The node's versions will be
        archived when the pipeline they belong to is archived.
        """
        super().archive()
        if not self.is_a_version:
            # We don't want to archive related objects for working versions, since they can be used in other pipelines
            return

        self._archive_related_params()

    def _get_version_details(self) -> VersionDetails:
        from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415 - circular: nodes.nodes→models

        node_name = self.params.get("name", self.type)
        if node_name == self.flow_id:
            node_name = self.type

        specs_by_param = {spec.param_name: spec for spec in get_versioned_param_specs(self.type)}
        param_versions = []
        for name, value in self.params.items():
            display_formatter = None
            if spec := specs_by_param.get(name):
                # Load the referenced record(s) for display
                name = spec.display_name
                value = spec.resolve_for_display(value)
            else:
                match name:
                    case "tools":
                        display_formatter = VersionFieldDisplayFormatters.format_tools
                    case "custom_actions":
                        # This is appended to the param_versions list separately
                        continue
                    case "name":
                        value = node_name

            param_versions.append(
                VersionField(group_name=node_name, name=name, raw_value=value, to_display=display_formatter),
            )

        if self.type == LLMResponseWithPrompt.__name__ and self.params.get("custom_actions"):
            param_versions.append(
                VersionField(
                    group_name=node_name,
                    name="custom_actions",
                    queryset=self.get_custom_action_operations(),
                    to_display=VersionFieldDisplayFormatters.format_custom_action_operation,
                )
            )

        return VersionDetails(
            instance=self,
            fields=param_versions,
        )

    def requires_attachment_tool(self) -> bool:
        """When a collection is linked, the attachment tool is required"""
        return self.params.get("collection_id") is not None

    def _archive_related_params(self):
        """
        Archive related params that were also versioned along with this node
        """
        for spec in get_versioned_param_specs(self.type):
            spec.archive_referenced_record(self.params)


class PipelineEventInputs(models.TextChoices):
    FULL_HISTORY = "full_history", "Full History"
    HISTORY_LAST_SUMMARY = "history_last_summary", "History to last summary"
    LAST_MESSAGE = "last_message", "Last message"


class PipelineChatHistoryTypes(models.TextChoices):
    NODE = "node", "Node"
    NAMED = "named", "Named"
    GLOBAL = "global", "Global"
    NONE = "none", "No History"


class PipelineChatHistoryModes(models.TextChoices):
    SUMMARIZE = "summarize", "Summarize"
    TRUNCATE_TOKENS = "truncate_tokens", "Truncate Tokens"
    MAX_HISTORY_LENGTH = "max_history_length", "Max History Length"


class PipelineChatHistory(BaseModel):
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="pipeline_chat_history")

    type = models.CharField(max_length=10, choices=PipelineChatHistoryTypes.choices)
    name = models.CharField(max_length=128, db_index=True)  # Either the name of the named history, or the node id

    def __str__(self):
        return f"Session: {self.session_id}, Type: {self.type}, Name: {self.name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("session", "type", "name"), name="unique_session_type_name"),
        ]
        ordering = ["-created_at"]

    def message_iterator(self) -> Iterator["PipelineChatMessages"]:
        yield from self.messages.order_by("-created_at").iterator(100)

    def get_messages_until_marker(self, marker: PipelineChatHistoryModes):
        messages = []
        for message in self.message_iterator():
            messages.append(message)
            if message.compression_marker == marker:
                break
        return messages

    def get_langchain_messages_until_marker(self, marker: PipelineChatHistoryModes):
        messages = self.get_messages_until_marker(marker)
        include_summary = marker == PipelineChatHistoryModes.SUMMARIZE
        langchain_messages_to_last_summary = [
            message for message_pair in messages for message in message_pair.as_langchain_messages(include_summary)
        ]
        return list(reversed(langchain_messages_to_last_summary))


class PipelineChatMessages(BaseModel):
    chat_history = models.ForeignKey(PipelineChatHistory, on_delete=models.CASCADE, related_name="messages")
    node_id = models.TextField()
    human_message = models.TextField()
    ai_message = models.TextField()
    summary = models.TextField(null=True)  # noqa: DJ001
    compression_marker = models.CharField(max_length=32, choices=PipelineChatHistoryModes.choices, blank=True)

    def __str__(self):
        if self.summary:
            return f"Human: {self.human_message}, AI: {self.ai_message}, System: {self.summary}"
        return f"Human: {self.human_message}, AI: {self.ai_message}"

    def as_tuples(self, include_summaries=True) -> list[tuple]:
        message_tuples = []
        if include_summaries and self.summary:
            message_tuples.append((ChatMessageType.SYSTEM.value, self.summary))
        message_tuples.extend(
            [
                (ChatMessageType.HUMAN.value, self.human_message),
                (ChatMessageType.AI.value, self.ai_message),
            ]
        )
        return message_tuples

    def as_langchain_messages(self, include_summary=True) -> list[BaseMessage]:
        """
        Converts this message instance into a list of Langchain `BaseMessage` objects.
        The message order is the reverse of the typical order because of where this
        method is called. The returned order is: [`AIMessage`, `HumanMessage`, `SystemMessage`].

        The `SystemMessage` represents the conversation summary and will only be
        included if it exists.
        """
        langchain_messages: list[BaseMessage] = [
            AIMessage(content=self.ai_message, additional_kwargs={"id": self.id, "node_id": self.node_id}),
            # An empty human message replays as an empty text content block, which Anthropic
            # rejects with a 400. See `ensure_non_empty_text`.
            HumanMessage(
                content=ensure_non_empty_text(self.human_message),
                additional_kwargs={"id": self.id, "node_id": self.node_id},
            ),
        ]
        if include_summary and self.summary:
            langchain_messages.append(
                SystemMessage(content=self.summary, additional_kwargs={"id": self.id, "node_id": self.node_id})
            )

        return langchain_messages

"""Validate and remove legacy resource ids from pipeline node JSON."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
SCALAR_RESOURCE_PARAMS = {
    "llm_provider_id": "llm_provider",
    "llm_provider_model_id": "llm_provider_model",
    "source_material_id": "source_material",
    "collection_id": "collection",
    "synthetic_voice_id": "synthetic_voice",
}
LIST_RESOURCE_PARAMS = {"collection_index_ids", "custom_actions"}
RESOURCE_PARAMS = frozenset((*SCALAR_RESOURCE_PARAMS, *LIST_RESOURCE_PARAMS))
TEAM_SCOPED_VOICE_SERVICES = frozenset({"OpenAIVoiceEngine", "ElevenLabs", "Intron", "MiniMax"})


@dataclass(frozen=True)
class ParsedNodeResources:
    """Normalized resource values supplied by one legacy node row."""

    scalar: dict[str, int | None]
    collection_indexes: set[int] | None
    custom_actions: set[tuple[int, str]] | None
    resource_keys: frozenset[str]


class ResourceMigrationError(RuntimeError):
    """Raised when legacy node data cannot be converted without losing a reference."""


def strip_node_resource_params(apps, schema_editor, *, dry_run: bool = False) -> None:
    """Validate every node before removing verified resource keys from its JSON.

    The first pass is read-only and aborts the migration with an anomaly report if any reference
    cannot be proven safe. A dry run stops after validation; otherwise each verified batch is
    committed transactionally so a bad row cannot partially convert its batch.
    """
    models = _historical_models(apps)
    database = schema_editor.connection.alias
    node_ids = _node_ids(models["Node"], database)

    anomalies = []
    for node_batch in _batches(node_ids):
        nodes = _load_nodes(models["Node"], node_batch, database)
        _parsed, batch_anomalies = _validate_batch(models, nodes, database)
        anomalies.extend(batch_anomalies)
    if anomalies:
        _raise_anomalies(anomalies)
    if dry_run:
        logger.info("Validated %s pipeline nodes; no resource params were changed", len(node_ids))
        return

    for node_batch in _batches(node_ids):
        nodes = _load_nodes(models["Node"], node_batch, database)
        parsed, anomalies = _validate_batch(models, nodes, database)
        if anomalies:
            _raise_anomalies(anomalies)
        _apply_batch(models, nodes, parsed, database)


def restore_node_resource_params(apps, schema_editor) -> None:
    """Restore resource keys from relations for a code rollback.

    This reverse path reconstructs values that still exist in the relational columns and through
    tables. It cannot recover an explicit null, an empty list, or a relation changed after the
    forward cutover; a database/JSON backup remains the authoritative rollback mechanism for those
    cases.
    """
    models = _historical_models(apps)
    database = schema_editor.connection.alias
    node_ids = _node_ids(models["Node"], database)
    for node_batch in _batches(node_ids):
        with transaction.atomic(using=database):
            node_model = models["Node"]
            nodes = list(_all_nodes(node_model, database).select_for_update().filter(id__in=node_batch).order_by("id"))
            _restore_batch(models, nodes, database)


def _historical_models(apps) -> dict[str, Any]:
    """Return the migration-state models used by the conversion."""
    return {
        name: apps.get_model(app_label, model_name)
        for name, app_label, model_name in (
            ("Node", "pipelines", "Node"),
            ("LlmProvider", "service_providers", "LlmProvider"),
            ("LlmProviderModel", "service_providers", "LlmProviderModel"),
            ("SourceMaterial", "experiments", "SourceMaterial"),
            ("SyntheticVoice", "experiments", "SyntheticVoice"),
            ("VoiceProvider", "service_providers", "VoiceProvider"),
            ("Collection", "documents", "Collection"),
            ("CustomAction", "custom_actions", "CustomAction"),
            ("CustomActionOperation", "custom_actions", "CustomActionOperation"),
        )
    }


def _node_ids(node_model, database: str) -> list[int]:
    """Return every node id, including archived and published-version rows."""
    manager = node_model.objects
    queryset = manager.get_all() if hasattr(manager, "get_all") else node_model._base_manager.all()
    return list(queryset.using(database).order_by("id").values_list("id", flat=True))


def _load_nodes(node_model, node_ids: list[int], database: str) -> list[Any]:
    """Load one batch with its pipeline team and lock it only during the write pass."""
    manager = node_model.objects
    queryset = manager.get_all() if hasattr(manager, "get_all") else node_model._base_manager.all()
    return list(queryset.using(database).select_related("pipeline").filter(id__in=node_ids).order_by("id"))


def _batches(values: list[int]):
    """Yield fixed-size slices so each successful write batch can commit independently."""
    for start in range(0, len(values), BATCH_SIZE):
        yield values[start : start + BATCH_SIZE]


def _validate_batch(models: dict[str, Any], nodes: list[Any], database: str):
    """Parse, validate, and compare one node batch without changing database state."""
    parsed_by_node = {}
    anomalies = []
    for node in nodes:
        parsed, node_anomalies = _parse_node(node)
        parsed_by_node[node.id] = parsed
        anomalies.extend(node_anomalies)
    if anomalies:
        return parsed_by_node, anomalies

    catalog = _load_catalog(models, nodes, parsed_by_node, database)
    for node in nodes:
        anomalies.extend(_validate_node(node, parsed_by_node[node.id], catalog))
    return parsed_by_node, anomalies


def _parse_node(node: Any) -> tuple[ParsedNodeResources, list[str]]:
    """Normalize JSON resource values and report malformed shapes before any relation is changed."""
    params = node.params or {}
    if not isinstance(params, dict):
        return ParsedNodeResources({}, None, None, frozenset()), [_anomaly(node, "params is not an object")]

    resource_keys = frozenset(params).intersection(RESOURCE_PARAMS)
    scalar, scalar_anomalies = _parse_scalar_resources(node, params)
    collection_indexes, collection_anomalies = _parse_collection_indexes(node, params)
    custom_actions, custom_action_anomalies = _parse_custom_actions(node, params)
    anomalies = scalar_anomalies + collection_anomalies + custom_action_anomalies

    return ParsedNodeResources(scalar, collection_indexes, custom_actions, resource_keys), anomalies


def _parse_scalar_resources(node: Any, params: dict) -> tuple[dict[str, int | None], list[str]]:
    """Normalize scalar resource ids and report malformed values."""
    scalar = {}
    anomalies = []
    for param_name in SCALAR_RESOURCE_PARAMS:
        if param_name not in params:
            continue
        value = params[param_name]
        if value in (None, ""):
            scalar[param_name] = None
            continue
        parsed = _as_int(value)
        if parsed is None:
            anomalies.append(_anomaly(node, f"{param_name} is not an integer: {value!r}"))
        else:
            scalar[param_name] = parsed
    return scalar, anomalies


def _parse_collection_indexes(node: Any, params: dict) -> tuple[set[int] | None, list[str]]:
    """Normalize the collection-index list and report malformed values."""
    if "collection_index_ids" not in params:
        return None, []
    raw_indexes = params["collection_index_ids"]
    if raw_indexes is None:
        return set(), []
    if not isinstance(raw_indexes, list):
        return None, [_anomaly(node, "collection_index_ids is not a list")]
    anomalies: list[str] = []
    return _parse_id_list(node, "collection_index_ids", raw_indexes, anomalies), anomalies


def _parse_custom_actions(node: Any, params: dict) -> tuple[set[tuple[int, str]] | None, list[str]]:
    """Normalize custom-action operation references and report malformed values."""
    if "custom_actions" not in params:
        return None, []
    raw_actions = params["custom_actions"]
    if raw_actions is None:
        return set(), []
    if not isinstance(raw_actions, list):
        return None, [_anomaly(node, "custom_actions is not a list")]
    anomalies = []
    custom_actions = set()
    for value in raw_actions:
        if not isinstance(value, str):
            anomalies.append(_anomaly(node, f"custom action reference is not a string: {value!r}"))
            continue
        action_id, separator, operation_id = value.partition(":")
        parsed_action_id = _as_int(action_id)
        if not separator or parsed_action_id is None or not operation_id:
            anomalies.append(_anomaly(node, f"malformed custom action reference: {value!r}"))
            continue
        reference = (parsed_action_id, operation_id)
        if reference in custom_actions:
            anomalies.append(_anomaly(node, f"duplicate custom action reference: {value!r}"))
        custom_actions.add(reference)
    return custom_actions, anomalies


def _parse_id_list(node: Any, param_name: str, values: list[Any], anomalies: list[str]) -> set[int]:
    """Parse a list-valued resource field while preserving all malformed-value anomalies."""
    parsed_values = set()
    for value in values:
        parsed = _as_int(value)
        if parsed is None:
            anomalies.append(_anomaly(node, f"{param_name} contains a non-integer: {value!r}"))
        else:
            parsed_values.add(parsed)
    return parsed_values


def _as_int(value: Any) -> int | None:
    """Convert only integer JSON values and integer strings, excluding booleans and floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _load_catalog(models, nodes, parsed_by_node, database):
    """Load all referenced resource rows and current relation sets in bounded batch queries."""
    node_model = models["Node"]
    node_ids = [node.id for node in nodes]
    scalar_ids = {name: set() for name in SCALAR_RESOURCE_PARAMS}
    index_ids = set()
    action_ids = set()
    for node in nodes:
        parsed = parsed_by_node[node.id]
        for name, value in parsed.scalar.items():
            if value is not None:
                scalar_ids[name].add(value)
        index_ids.update(parsed.collection_indexes or ())
        for action_id, _operation_id in parsed.custom_actions or ():
            action_ids.add(action_id)

    scalar_models = {
        "llm_provider_id": models["LlmProvider"],
        "llm_provider_model_id": models["LlmProviderModel"],
        "source_material_id": models["SourceMaterial"],
        "collection_id": models["Collection"],
        "synthetic_voice_id": models["SyntheticVoice"],
    }
    catalog = {
        "llm_provider_id": _rows(
            scalar_models["llm_provider_id"], scalar_ids["llm_provider_id"], database, "id", "team_id", "type"
        ),
        "llm_provider_model_id": _rows(
            scalar_models["llm_provider_model_id"],
            scalar_ids["llm_provider_model_id"],
            database,
            "id",
            "team_id",
            "type",
            "deprecated",
        ),
        "source_material_id": _rows(
            scalar_models["source_material_id"],
            scalar_ids["source_material_id"],
            database,
            "id",
            "team_id",
            "is_archived",
        ),
        "collection_id": _rows(
            scalar_models["collection_id"],
            scalar_ids["collection_id"],
            database,
            "id",
            "team_id",
            "is_index",
            "is_archived",
        ),
        "synthetic_voice_id": _rows(
            scalar_models["synthetic_voice_id"],
            scalar_ids["synthetic_voice_id"],
            database,
            "id",
            "service",
            "voice_provider_id",
        ),
        "voice_providers": {},
        "collections_indexes": _rows(
            models["Collection"], index_ids, database, "id", "team_id", "is_index", "is_archived"
        ),
        "custom_actions": _rows(
            models["CustomAction"], action_ids, database, "id", "team_id", "allowed_operations", "_operations"
        ),
        "team_provider_types": _team_provider_types(models["LlmProvider"], nodes, database),
        "team_voice_provider_types": _team_voice_provider_types(models["VoiceProvider"], nodes, database),
        "current_indexes": _current_collection_indexes(node_model, node_ids, database),
        "current_actions": _current_custom_actions(models["CustomActionOperation"], node_ids, database),
    }
    voice_provider_ids = {
        row["voice_provider_id"]
        for row in catalog["synthetic_voice_id"].values()
        if row["voice_provider_id"] is not None
    }
    catalog["voice_providers"] = _rows(models["VoiceProvider"], voice_provider_ids, database, "id", "team_id", "type")
    return catalog


def _rows(model, ids: set[int], database: str, *fields: str) -> dict[int, dict[str, Any]]:
    """Load selected rows through the archive-inclusive base manager."""
    if not ids:
        return {}
    return {row["id"]: row for row in model._base_manager.using(database).filter(id__in=ids).values(*fields)}


def _team_provider_types(provider_model, nodes, database) -> dict[int, set[str]]:
    """Load provider types available to each pipeline team in the current batch."""
    team_ids = {node.pipeline.team_id for node in nodes}
    result = defaultdict(set)
    for row in provider_model._base_manager.using(database).filter(team_id__in=team_ids).values("team_id", "type"):
        result[row["team_id"]].add(row["type"])
    return result


def _team_voice_provider_types(provider_model, nodes, database) -> dict[int, set[str]]:
    """Load voice-provider services available to each pipeline team in the current batch."""
    team_ids = {node.pipeline.team_id for node in nodes}
    result = defaultdict(set)
    for row in provider_model._base_manager.using(database).filter(team_id__in=team_ids).values("team_id", "type"):
        result[row["team_id"]].add(row["type"].lower())
    return result


def _current_collection_indexes(node_model, node_ids: list[int], database) -> dict[int, set[int]]:
    """Read existing collection-index through rows grouped by node."""
    field = node_model._meta.get_field("collection_indexes")
    through = field.remote_field.through
    result = defaultdict(set)
    for row in through._default_manager.using(database).filter(node_id__in=node_ids).values("node_id", "collection_id"):
        result[row["node_id"]].add(row["collection_id"])
    return result


def _current_custom_actions(operation_model, node_ids: list[int], database) -> dict[int, set[tuple[int, str]]]:
    """Read existing custom-action operation rows grouped by node."""
    result = defaultdict(set)
    for row in (
        operation_model._base_manager.using(database)
        .filter(node_id__in=node_ids)
        .values("node_id", "custom_action_id", "operation_id")
    ):
        result[row["node_id"]].add((row["custom_action_id"], row["operation_id"]))
    return result


def _validate_node(node, parsed: ParsedNodeResources, catalog) -> list[str]:
    """Validate ownership, resource type, and relation agreement for one node."""
    return [
        *_validate_scalar_references(node, parsed, catalog),
        *_validate_collection_indexes(node, parsed, catalog),
        *_validate_custom_actions(node, parsed, catalog),
    ]


def _validate_scalar_references(node, parsed: ParsedNodeResources, catalog) -> list[str]:
    """Validate scalar resource ownership and agreement with existing FK columns."""
    anomalies = []
    team_id = node.pipeline.team_id
    for param_name, field_name in SCALAR_RESOURCE_PARAMS.items():
        if param_name not in parsed.scalar:
            continue
        value = parsed.scalar[param_name]
        if value is not None:
            row = catalog[param_name].get(value)
            if row is None:
                anomalies.append(_anomaly(node, f"{param_name} references missing id {value}"))
            else:
                anomalies.extend(_validate_scalar_resource(node, param_name, row, team_id, catalog))
        current = getattr(node, f"{field_name}_id")
        if current not in (None, value) and current != value:
            anomalies.append(_anomaly(node, f"{param_name} conflicts with relation id {current}"))

    return anomalies


def _validate_collection_indexes(node, parsed: ParsedNodeResources, catalog) -> list[str]:
    """Validate collection-index ownership/type and agreement with the existing M2M rows."""
    if parsed.collection_indexes is None:
        return []
    anomalies = []
    team_id = node.pipeline.team_id
    for collection_id in parsed.collection_indexes:
        row = catalog["collections_indexes"].get(collection_id)
        if row is None:
            anomalies.append(_anomaly(node, f"collection_index_ids references missing id {collection_id}"))
        elif row["team_id"] != team_id or not row["is_index"]:
            anomalies.append(_anomaly(node, f"collection_index_ids references an invalid collection {collection_id}"))
    current = catalog["current_indexes"].get(node.id, set())
    if current and current != parsed.collection_indexes:
        anomalies.append(_anomaly(node, f"collection_index_ids conflicts with relation ids {sorted(current)}"))
    return anomalies


def _validate_custom_actions(node, parsed: ParsedNodeResources, catalog) -> list[str]:
    """Validate custom-action ownership/operations and agreement with operation rows."""
    if parsed.custom_actions is None:
        return []
    anomalies = []
    team_id = node.pipeline.team_id
    current = catalog["current_actions"].get(node.id, set())
    for action_id, operation_id in parsed.custom_actions:
        action = catalog["custom_actions"].get(action_id)
        if action is None:
            anomalies.append(_anomaly(node, f"custom_actions references missing action {action_id}"))
        elif action["team_id"] != team_id:
            anomalies.append(_anomaly(node, f"custom_actions references another team's action {action_id}"))
        elif (action_id, operation_id) not in current and not _action_contains_operation(action, operation_id):
            anomalies.append(
                _anomaly(node, f"custom_actions references unavailable operation {action_id}:{operation_id}")
            )
    if current and current != parsed.custom_actions:
        anomalies.append(_anomaly(node, f"custom_actions conflicts with relation rows {sorted(current)}"))
    return anomalies


def _validate_scalar_resource(node, param_name, row, team_id, catalog) -> list[str]:
    """Validate team ownership and resource-family-specific constraints for one scalar reference."""
    if param_name == "llm_provider_id":
        return [] if row["team_id"] == team_id else [_anomaly(node, f"{param_name} references another team's provider")]
    if param_name == "llm_provider_model_id":
        errors = []
        if row["team_id"] not in (None, team_id):
            errors.append(_anomaly(node, f"{param_name} references another team's model"))
        if row["deprecated"]:
            errors.append(_anomaly(node, f"{param_name} references a deprecated model"))
        if row["type"] not in catalog["team_provider_types"].get(team_id, set()):
            errors.append(_anomaly(node, f"{param_name} has no usable provider type for the team"))
        return errors
    if param_name in {"source_material_id", "collection_id"} and row["team_id"] != team_id:
        return [_anomaly(node, f"{param_name} references another team's resource")]
    if param_name == "collection_id" and row["is_index"]:
        return [_anomaly(node, "collection_id references an index collection")]
    if param_name == "synthetic_voice_id":
        provider_id = row["voice_provider_id"]
        if provider_id is None:
            return (
                []
                if row["service"] not in TEAM_SCOPED_VOICE_SERVICES
                and row["service"].lower() in catalog.get("team_voice_provider_types", {}).get(team_id, set())
                else [_anomaly(node, "synthetic voice is not reachable by the pipeline team")]
            )
        provider = catalog["voice_providers"].get(provider_id)
        return (
            []
            if provider and provider["team_id"] == team_id and provider["type"].lower() == row["service"].lower()
            else [_anomaly(node, "synthetic voice provider does not match the pipeline team or service")]
        )
    return []


def _action_contains_operation(action: dict[str, Any], operation_id: str) -> bool:
    """Return whether a custom action currently publishes and allows an operation."""
    allowed = action["allowed_operations"] or []
    operations = {operation.get("operation_id") for operation in action["_operations"] or []}
    return operation_id in allowed and operation_id in operations


def _apply_batch(models, nodes, parsed_by_node, database) -> None:
    """Revalidate locked rows, then remove only their verified resource keys."""
    node_model = models["Node"]
    operation_model = models["CustomActionOperation"]
    index_field = node_model._meta.get_field("collection_indexes")
    through = index_field.remote_field.through
    with transaction.atomic(using=database):
        locked_nodes = list(
            _all_nodes(node_model, database)
            .select_for_update()
            .select_related("pipeline")
            .filter(id__in=[node.id for node in nodes])
        )
        parsed_by_node, anomalies = _validate_batch(models, locked_nodes, database)
        if anomalies:
            _raise_anomalies(anomalies)
        scalar_fields = set()
        for node in locked_nodes:
            parsed = parsed_by_node[node.id]
            for param_name, field_name in SCALAR_RESOURCE_PARAMS.items():
                if param_name in parsed.scalar:
                    setattr(node, f"{field_name}_id", parsed.scalar[param_name])
                    scalar_fields.add(f"{field_name}_id")
            if parsed.resource_keys:
                node.params = {key: value for key, value in (node.params or {}).items() if key not in RESOURCE_PARAMS}
        if locked_nodes:
            fields = ["params", *sorted(scalar_fields)]
            node_model._base_manager.using(database).bulk_update(locked_nodes, fields, batch_size=BATCH_SIZE)

        for node in locked_nodes:
            parsed = parsed_by_node[node.id]
            if parsed.collection_indexes is not None and not _current_collection_indexes(
                node_model, [node.id], database
            ).get(node.id, set()):
                through._default_manager.using(database).bulk_create(
                    [
                        through(node_id=node.id, collection_id=collection_id)
                        for collection_id in parsed.collection_indexes
                    ],
                    ignore_conflicts=True,
                )
            if parsed.custom_actions is not None and not _current_custom_actions(
                operation_model, [node.id], database
            ).get(node.id, set()):
                action_rows = _action_rows_for_node(models, node, parsed.custom_actions, database)
                operation_model._base_manager.using(database).bulk_create(action_rows, ignore_conflicts=True)


def _restore_batch(models, nodes, database) -> None:
    """Rebuild resource JSON keys for one locked rollback batch from relation rows."""
    if not nodes:
        return
    node_model = models["Node"]
    node_ids = [node.id for node in nodes]
    index_field = node_model._meta.get_field("collection_indexes")
    through = index_field.remote_field.through
    indexes_by_node = defaultdict(list)
    for row in (
        through._default_manager.using(database)
        .filter(node_id__in=node_ids)
        .order_by("node_id", "collection_id")
        .values("node_id", "collection_id")
    ):
        indexes_by_node[row["node_id"]].append(row["collection_id"])

    actions_by_node = defaultdict(list)
    for row in (
        models["CustomActionOperation"]
        ._base_manager.using(database)
        .filter(node_id__in=node_ids)
        .order_by("node_id", "custom_action_id", "operation_id")
        .values("node_id", "custom_action_id", "operation_id")
    ):
        actions_by_node[row["node_id"]].append(f"{row['custom_action_id']}:{row['operation_id']}")

    for node in nodes:
        params = dict(node.params or {})
        for param_name, field_name in SCALAR_RESOURCE_PARAMS.items():
            value = getattr(node, f"{field_name}_id", None)
            if value is not None:
                params[param_name] = value
        if node.id in indexes_by_node:
            params["collection_index_ids"] = indexes_by_node[node.id]
        if node.id in actions_by_node:
            params["custom_actions"] = actions_by_node[node.id]
        node.params = params
    node_model._base_manager.using(database).bulk_update(nodes, ["params"], batch_size=BATCH_SIZE)


def _all_nodes(node_model, database):
    """Return the archive-inclusive node queryset for the migration database."""
    manager = node_model.objects
    queryset = manager.get_all() if hasattr(manager, "get_all") else node_model._base_manager.all()
    return queryset.using(database)


def _action_rows_for_node(models, node, references, database):
    """Build operation rows, preserving an operation schema for historical node versions."""
    action_model = models["CustomAction"]
    actions = {
        row["id"]: row
        for row in action_model._base_manager.using(database)
        .filter(id__in={action_id for action_id, _operation_id in references})
        .values("id", "_operations")
    }
    rows = []
    for action_id, operation_id in references:
        operation_schema = next(
            (
                operation
                for operation in actions[action_id]["_operations"] or []
                if operation.get("operation_id") == operation_id
            ),
            {},
        )
        rows.append(
            models["CustomActionOperation"](
                node_id=node.id,
                custom_action_id=action_id,
                operation_id=operation_id,
                _operation_schema=operation_schema if node.working_version_id else {},
            )
        )
    return rows


def _anomaly(node, detail: str) -> str:
    """Format one anomaly with enough identity to locate the source row."""
    return f"node={node.id} pipeline={node.pipeline_id} flow_id={node.flow_id}: {detail}"


def _raise_anomalies(anomalies: list[str]) -> None:
    """Log the complete anomaly report and abort before any batch is written."""
    for anomaly in anomalies:
        logger.error("node resource migration anomaly: %s", anomaly)
    preview = "; ".join(anomalies[:10])
    raise ResourceMigrationError(f"Found {len(anomalies)} node resource anomalies; first entries: {preview}")

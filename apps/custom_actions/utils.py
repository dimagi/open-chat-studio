from copy import deepcopy
from typing import TypedDict

from django.core.exceptions import FieldDoesNotExist, ValidationError


class CustomActionOperationInfo(TypedDict):
    custom_action_id: int
    operation_id: str


def get_custom_action_operation_choices(team):
    """Make grouped choices for operations allowed by custom actions."""
    choices = []
    for action in team.customaction_set.defer("api_schema", "prompt").all():
        group = []
        all_ops = action.get_operations_by_id()
        for operation_id in action.allowed_operations:
            operation = all_ops.get(operation_id)
            if not operation:
                continue

            model_id = make_model_id(None, action.id, operation_id)
            group.append((model_id, f"{action.name}: {operation.description}"))
        if group:
            choices.append((action.name, group))
    return choices


def initialize_form_for_custom_actions(team, form):
    form.fields["custom_action_operations"].choices = get_custom_action_operation_choices(team)
    if form.instance and form.instance.id:
        form.initial["custom_action_operations"] = [
            op.get_model_id(with_holder=False) for op in form.instance.custom_action_operations.all()
        ]


def clean_custom_action_operations(form):
    operations = form.cleaned_data["custom_action_operations"]
    parsed_operations = []
    for op in operations:
        try:
            action_id, operation_id = op.split(":", 1)
        except ValueError:
            raise ValidationError("Invalid format for custom action operation")
        try:
            action_id = int(action_id)
        except ValueError:
            raise ValidationError("Invalid custom action operation")
        parsed_operations.append({"custom_action_id": action_id, "operation_id": operation_id})
    return parsed_operations


def set_custom_actions(holder, custom_action_infos: list[CustomActionOperationInfo]):
    """
    Set the custom actions for the holder.

    Args:
        holder: The holder model instance, an Experiment or an OpenAiAssistant.
        custom_action_infos: A list of dictionaries containing the custom action information.
    """
    from apps.custom_actions.models import CustomActionOperation

    if not hasattr(holder, "custom_action_operations"):
        raise FieldDoesNotExist(f"{holder.__class__.__name__} does not have a custom_action_operations field")

    def _clear_query_cache():
        holder.refresh_from_db(fields=["custom_action_operations"])

    model_field = holder._meta.get_field("custom_action_operations")
    holder_kwarg = model_field.remote_field.name
    holder_kwargs = {holder_kwarg: holder}
    if not custom_action_infos:
        CustomActionOperation.objects.filter(**holder_kwargs).delete()
        _clear_query_cache()
        return

    action_ops_by_id = {action_op.get_model_id(): action_op for action_op in holder.custom_action_operations.all()}
    for info in custom_action_infos:
        op_id = make_model_id(holder.id, **info)
        op = action_ops_by_id.pop(op_id, None)
        if not op:
            CustomActionOperation.objects.create(**holder_kwargs, **info)

    if action_ops_by_id:
        old_ids = [old.id for old in action_ops_by_id.values()]
        CustomActionOperation.objects.filter(id__in=old_ids).delete()

    _clear_query_cache()


def make_model_id(holder_id, custom_action_id, operation_id):
    ret = f"{custom_action_id}:{operation_id}"
    if holder_id:
        ret = f"{holder_id}:{ret}"
    return ret


def get_standalone_schema_for_action_operation(action_operation):
    action = action_operation.custom_action
    ops_by_id = action.get_operations_by_id()
    operation = ops_by_id.get(action_operation.operation_id)
    if not operation:
        raise ValidationError("Custom action operation is no longer available")

    return get_standalone_spec(action.api_schema, operation.path, operation.method)


def get_standalone_spec(openapi_spec: dict, path: str, method: str):
    """Returns a standalone OpenAPI spec for a single operation."""
    openapi_spec = trim_spec(openapi_spec)
    info = openapi_spec["info"]
    info["title"] += f" - {method} {path}"
    info["description"] = f"Standalone OpenAPI spec for {method} {path}"
    paths = openapi_spec.pop("paths")
    openapi_spec["paths"] = {path: {method: paths[path][method]}}
    return openapi_spec


def trim_spec(openapi_spec: dict) -> dict:
    """Removes unnecessary keys from the OpenAPI spec.
    If there are any refs in the schema, they will be resolved.
    """
    openapi_spec = resolve_references(openapi_spec)
    top_level_keys = ["openapi", "info", "servers", "paths"]
    for key in list(openapi_spec.keys()):
        if key not in top_level_keys:
            del openapi_spec[key]

    operation_keys = ["parameters", "requestBody", "tags", "summary", "description", "operationId"]
    for path, methods in openapi_spec["paths"].items():
        for method, details in methods.items():
            for key in list(details.keys()):
                if key not in operation_keys:
                    del details[key]

    return openapi_spec


def resolve_references(openapi_spec: dict) -> dict:
    """
    Resolves all $ref references in an OpenAPI specification document.

    Args:
        openapi_spec: The OpenAPI specification document.

    Returns:
        The OpenAPI specification document with all $ref references resolved.
    """

    def resolve_ref(data: dict, path: str) -> dict:
        if "$ref" in data:
            ref = data["$ref"]
            if not ref[0] == "#":
                raise ValueError(f"External references are not supported: {ref}")

            ref_path = ref[1:].split("/")[1:]
            current = openapi_spec
            for p in ref_path:
                current = current[p]
            return deepcopy(current)
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict | list):
                    data[k] = resolve_ref(v, f"{path}/{k}")
                else:
                    data[k] = v
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict | list):
                    data[i] = resolve_ref(item, f"{path}/{i}")
                else:
                    data[i] = item
        return data

    return resolve_ref(deepcopy(openapi_spec), "")

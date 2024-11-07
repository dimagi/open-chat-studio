from typing import TypedDict

from django.core.exceptions import FieldDoesNotExist, ValidationError
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec
from pydantic import BaseModel


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


class APIOperationDetails(BaseModel):
    operation_id: str
    description: str
    path: str
    method: str

    def __str__(self):
        return f"{self.method.upper()}: {self.description}"


def get_operations_from_spec_dict(spec_dict) -> list[APIOperationDetails]:
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    return get_operations_from_spec(spec)


def get_operations_from_spec(spec) -> list[APIOperationDetails]:
    operations = []
    for path in spec.paths:
        for method in spec.get_methods_for_path(path):
            op = APIOperation.from_openapi_spec(spec, path, method)
            operations.append(
                APIOperationDetails(operation_id=op.operation_id, description=op.description, path=path, method=method)
            )
    return operations

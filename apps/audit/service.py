from django.core.exceptions import FieldDoesNotExist
from django_pydantic_field.v2.fields import PydanticSchemaField
from field_audit import AuditService as AuditServiceOriginal
from field_audit.global_context import is_audit_enabled
from pydantic_core import to_jsonable_python


class AuditService(AuditServiceOriginal):
    """Custom AuditService to support serialization of pydantic models"""

    def attach_initial_values(self, instance):
        if not is_audit_enabled():
            return
        super().attach_initial_values(instance)

    def get_field_value(self, instance, field_name, bootstrap=False):
        field = instance._meta.get_field(field_name)
        if isinstance(field, PydanticSchemaField):
            return to_jsonable_python(field.value_from_object(instance))
        return super().get_field_value(instance, field_name, bootstrap)

    def make_audit_event_from_values(self, old_values, new_values, object_pk, object_cls, request):
        # The delete path fetches values via ``QuerySet.values()``, which returns pydantic objects for
        # SchemaFields (bypassing ``get_field_value``). Serialize them so the delta is JSON safe.
        return super().make_audit_event_from_values(
            _values_to_json_safe(old_values, object_cls),
            _values_to_json_safe(new_values, object_cls),
            object_pk,
            object_cls,
            request,
        )


def _values_to_json_safe(values, object_cls):
    return {
        name: to_jsonable_python(value) if _is_schema_field(object_cls, name) else value
        for name, value in values.items()
    }


def _is_schema_field(object_cls, name):
    try:
        field = object_cls._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return isinstance(field, PydanticSchemaField)

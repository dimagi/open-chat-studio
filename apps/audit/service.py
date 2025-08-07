import json

from django_pydantic_field.v2.fields import PydanticSchemaField
from field_audit import AuditService as AuditServiceOriginal
from pydantic import BaseModel


class AuditService(AuditServiceOriginal):
    """Custom AuditService to support serialization of pydantic models"""

    def get_field_value(self, instance, field_name, bootstrap=False):
        field = instance._meta.get_field(field_name)
        if isinstance(field, PydanticSchemaField):
            value = field.value_from_object(instance)
            if value and isinstance(value, BaseModel):
                return json.loads(value.model_dump_json())
            return value
        return super().get_field_value(instance, field_name, bootstrap)

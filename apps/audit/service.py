import json

from field_audit import AuditService as AuditServiceOriginal
from pydantic import BaseModel


class AuditService(AuditServiceOriginal):
    """Custom AuditService to support serialization of pydantic models"""

    def get_field_value(self, instance, field_name, bootstrap=False):
        value = super().get_field_value(instance, field_name, bootstrap)
        if isinstance(value, BaseModel):
            return json.loads(value.model_dump_json())
        return value

# Django model change auditing

This project uses `django-field-audit` to track changes to specific Django models.

See the `apps.experiments.Experiment` for an example.

The basic pattern is as follows:

```python
from apps.audit.decorators import audit_fields

class MyModelManager(AuditingManager):
    pass

@audit_fields("field_a", "field_b", audit_special_queryset_writes=True)
class MyModel(BaseTeamModel):
    # Define audit fields in model_audit_fields.py
    objects = MyModelManager()
```

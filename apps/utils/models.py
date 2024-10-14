from django.db import models


class VersioningMixin:
    def compare_with_model(self, new: models.Model, exclude_fields: list[str]) -> set:
        """
        Compares the field values of between `self` and `new`, excluding those in `exclude_fields`. Note that the fields
        names in `exclude_fields` should match those of the attribute on the django model i.e. those you get with
        ```
        [field.attname for field in model_instance._meta.fields]
        ```
        """
        model_fields = [field.attname for field in self._meta.fields if field.attname not in exclude_fields]
        changed_fields = set([])
        for field_name in model_fields:
            if getattr(self, field_name) != getattr(new, field_name):
                changed_fields.add(field_name)

        return changed_fields


class BaseModel(models.Model, VersioningMixin):
    """
    Base model that includes default created / updated timestamps.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

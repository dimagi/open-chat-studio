from django.db import models


class VersioningMixin:
    def compare_with_model(self, new: models.Model, exclude_fields: list[str]) -> set:
        """
        Compares the field values of between `self` and `new`, excluding those in `exclude_fields`
        """
        model_fields = [field for field in self._meta.get_fields() if field.name not in exclude_fields]
        changed_fields = set([])
        for field in model_fields:
            if hasattr(field, "field"):
                # These fields are "pseudo" fields, present on this object because another object has a FK to this one
                continue
            elif field.many_to_many or field.one_to_many:
                # TODO: refactor this piece
                current_values_queryset = getattr(self, field.attname)
                new_values_queryset = getattr(new, field.attname)
                current_value = set(current_values_queryset.values_list("id", flat=True))
                new_value = set(new_values_queryset.values_list("id", flat=True))
            else:
                current_value = getattr(self, field.attname)
                new_value = getattr(new, field.attname)

            if new_value != current_value:
                changed_fields.add(field.name)
        return changed_fields


class BaseModel(models.Model, VersioningMixin):
    """
    Base model that includes default created / updated timestamps.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

from typing import Self

from django.db import models


class VersioningMixin:
    def compare_with_model(self, new: Self, exclude_fields: list[str], early_abort=False) -> set[str]:
        """
        Compares the field values of between `self` and `new`, excluding those in `exclude_fields`
        """
        model_fields = [field for field in self._meta.get_fields() if field.name not in exclude_fields]
        changed_fields = set([])
        for field in model_fields:
            if hasattr(field, "field"):
                # These are reverse relations, so let's ignore them
                continue

            if field.many_to_many or field.one_to_many:
                current_values = getattr(self, field.attname)
                if new:
                    new_values = getattr(new, field.attname)
                    new_value = set(new_values.values_list("id", flat=True))
                else:
                    new_value = set([])
                current_value = set(current_values.values_list("id", flat=True))
            else:
                current_value = getattr(self, field.attname)
                new_value = getattr(new, field.attname) if new else None

            if new_value != current_value:
                changed_fields.add(field.name)
                if early_abort:
                    return changed_fields
        return changed_fields

    def is_versioned(self) -> bool:
        """A simple check to see if the model is versioned"""
        return hasattr(self, "working_version")


class UpdatedAtOnPartialSaveMixin:
    """Write ``updated_at`` on ``save(update_fields=...)`` as well as on a full save.

    Django writes an ``auto_now`` field only when ``update_fields`` names it. The v2 sync API pages
    models with this mixin by ``updated_at``, so every write has to move it.
    """

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields and "updated_at" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "updated_at"]
        super().save(*args, **kwargs)


class BaseModel(models.Model, VersioningMixin):
    """
    Base model that includes default created / updated timestamps.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

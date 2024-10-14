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
        model_fields = [field.attname for field in self._meta.fields]
        original_dict, new_dict = self.__dict__, new.__dict__
        changed_fields = set([])
        for field_name, field_value in original_dict.items():
            if field_name not in model_fields:
                continue

            if field_name in exclude_fields:
                continue
            if field_value != new_dict[field_name]:
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
